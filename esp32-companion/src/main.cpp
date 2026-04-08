#include <Arduino.h>
#include "config.h"
#include "companion.h"
#include "display.h"
#include "buttons.h"
#include "ble_battle.h"
#include "duat_sync.h"
#include "epaper_driver_bsp.h"

// ── App states ────────────────────────────────────────────────
enum AppState {
    APP_BOOT,
    APP_COMPANION,
    APP_MENU,
    APP_STATS,
    APP_BATTLE_MENU,    // choose scan or advertise
    APP_BATTLE_SCAN,
    APP_BATTLE_WAIT,
    APP_BATTLE_FIGHT,
    APP_BATTLE_RESULT,
    APP_SYNC,
    APP_SETTINGS,
};

// ── Globals ───────────────────────────────────────────────────
static epaper_driver_display* epd;
static CompanionDisplay*      disp;

static Companion     companion;
static DeviceConfig  device_cfg;
static AppState      app_state = APP_BOOT;
static int           menu_idx  = 0;
static int           settings_idx = 0;
static bool          duat_online  = false;

static BattleRecord  battle_queue[MAX_PENDING_BATTLES];
static int           battle_queue_len = 0;

static uint32_t last_full_refresh = 0;
static uint32_t last_duat_sync    = 0;
static uint32_t boot_time         = 0;

// For battle fight animation
static int  fight_my_hp    = 0;
static int  fight_opp_hp   = 0;
static int  fight_round    = 0;
static char fight_action[32] = "";

// For scan results display
static const char* scan_names[MAX_OPPONENTS];

// ── Helpers ───────────────────────────────────────────────────
static void set_state(AppState s) {
    app_state = s;
}

static void do_sync(bool show_screen) {
    if (show_screen) screen_sync(disp, "Connecting WiFi...");

    bool ok = duat_connect_wifi();
    if (!ok) {
        duat_online = false;
        if (show_screen) {
            screen_sync(disp, "WiFi failed");
            delay(2000);
        }
        set_state(APP_COMPANION);
        return;
    }
    duat_online = true;

    if (show_screen) screen_sync(disp, "Fetching companion...");

    if (duat_fetch_companion(device_cfg.comp_id, &companion)) {
        if (show_screen) {
            screen_sync(disp, "Synced OK!");
            delay(1500);
        }
    } else {
        if (show_screen) {
            screen_sync(disp, "Fetch failed");
            delay(1500);
        }
    }

    // Flush pending battle records
    if (battle_queue_len > 0) {
        if (show_screen) screen_sync(disp, "Syncing battles...");
        duat_flush_queue(device_cfg.device_id, battle_queue, battle_queue_len);
    }

    if (show_screen) set_state(APP_COMPANION);
}

static void queue_battle_result(bool won, int rounds, int hp, const char* opp) {
    if (battle_queue_len >= MAX_PENDING_BATTLES) return;
    BattleRecord& r = battle_queue[battle_queue_len++];
    strlcpy(r.opponent_name, opp, sizeof(r.opponent_name));
    r.won          = won;
    r.rounds       = rounds;
    r.hp_remaining = hp;
    r.timestamp    = millis() / 1000;
    r.synced       = false;
}

static void animate_battle() {
    // Play through the battle simulation frame by frame
    const Companion* me  = &companion;
    BleOpponent&     opp = ble_opponent;

    Companion opp_c;
    strlcpy(opp_c.id, opp.comp_id, sizeof(opp_c.id));
    opp_c.hp      = opp.hp;
    opp_c.max_hp  = opp.max_hp;
    opp_c.attack  = opp.attack;
    opp_c.defense = opp.defense;

    // Re-run simulation to get per-round state (same seed = same result)
    // We already have the final result in ble_result; animate steps
    int aHP = me->hp;
    int bHP = opp_c.hp;
    srand(0); // we don't have the seed here — use result directly for animation
    // Just show final result after a brief fight display
    fight_round  = ble_result.rounds;
    fight_my_hp  = ble_result.attacker_hp;
    fight_opp_hp = ble_result.defender_hp;

    bool i_won = (ble_result.winner == BATTLE_ATTACKER);
    snprintf(fight_action, sizeof(fight_action),
             i_won ? "You win!" : (ble_result.winner == BATTLE_DRAW ? "Draw!" : "You lose!"));

    // Show fight screen briefly then jump to result
    screen_battle_fight(disp, me, &opp_c, fight_round, fight_my_hp, fight_opp_hp, fight_action);
    delay(2500);

    // Queue result
    bool won = (ble_result.winner == BATTLE_ATTACKER);
    queue_battle_result(won, ble_result.rounds,
                        won ? ble_result.attacker_hp : ble_result.defender_hp,
                        opp.name);

    // Update local stats
    if (won) {
        companion.wins++;
        companion.xp += 20 + ble_result.rounds;
        if (companion.xp >= companion.xp_to_next) {
            companion.level++;
            companion.xp -= companion.xp_to_next;
            companion.xp_to_next = companion.level * 100;
            companion.max_hp += 3;
            companion.hp     = companion.max_hp;
            companion.attack++;
        }
    } else {
        companion.losses++;
        int hp_loss = companion.max_hp / 4;
        companion.hp = max(1, companion.hp - hp_loss);
    }

    screen_battle_result(disp, won, ble_result.rounds, opp.name);
    set_state(APP_BATTLE_RESULT);
}

// ── Setup ─────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    Serial.println("Raven OS Companion Device booting...");

    // Load device config
    device_config_load(&device_cfg);
    Serial.printf("Device: %s  Companion: %s\n",
                  device_cfg.device_id, device_cfg.comp_id);

    // Load default companion
    if (strcmp(device_cfg.comp_id, "griffin") == 0) {
        companion_default_griffin(&companion);
    } else {
        companion_default_raven(&companion);
    }

    // Init e-paper
    custom_lcd_spi_t spi_cfg = {};
    spi_cfg.cs         = EPD_CS_PIN;
    spi_cfg.dc         = EPD_DC_PIN;
    spi_cfg.rst        = EPD_RST_PIN;
    spi_cfg.busy       = EPD_BUSY_PIN;
    spi_cfg.mosi       = EPD_MOSI_PIN;
    spi_cfg.scl        = EPD_SCK_PIN;
    spi_cfg.spi_host   = (int)SPI2_HOST;
    spi_cfg.buffer_len = EPD_WIDTH * EPD_HEIGHT / 8;  // 5000 bytes, 1-bit

    epd  = new epaper_driver_display(EPD_WIDTH, EPD_HEIGHT, spi_cfg);
    disp = new CompanionDisplay(epd);

    display_init(disp);

    // Buttons
    buttons_init();

    // Boot screen
    screen_boot(disp, device_cfg.comp_id);
    boot_time = millis();

    // Init BLE
    ble_init(&companion);

    // Try WiFi sync in background (non-blocking attempt)
    set_state(APP_BOOT);
}

// ── Loop ──────────────────────────────────────────────────────
void loop() {
    BtnEvent ev = buttons_poll();
    uint32_t now = millis();

    // ── BOOT — wait 2s then auto-progress ────────────────────
    if (app_state == APP_BOOT) {
        if (now - boot_time > 2000) {
            // Quick non-blocking WiFi check
            do_sync(false);
            set_state(APP_COMPANION);
            screen_companion(disp, &companion, duat_online);
        }
        return;
    }

    // ── Background: periodic Duat sync ───────────────────────
    if (now - last_duat_sync > DUAT_SYNC_INTERVAL_MS) {
        last_duat_sync = now;
        do_sync(false);
    }

    // ── Background: periodic full EPD refresh ────────────────
    if (now - last_full_refresh > FULL_REFRESH_INTERVAL_MS) {
        last_full_refresh = now;
        epd->EPD_Init();  // re-init clears ghosting
    }

    // ── BLE tick ─────────────────────────────────────────────
    ble_tick();

    // If BLE scan just finished
    if (app_state == APP_BATTLE_SCAN && ble_state == BLE_IDLE) {
        for (int i = 0; i < ble_found_count; i++) scan_names[i] = ble_found[i].name;
        screen_battle_scan(disp, scan_names, ble_found_count);
    }

    // If BLE battle done (async completion while advertising)
    if (app_state == APP_BATTLE_WAIT && ble_state == BLE_DONE) {
        animate_battle();
        return;
    }

    if (ev == BTN_NONE) return;

    // ── State-specific input handling ────────────────────────
    switch (app_state) {

    case APP_COMPANION:
        if (ev == BTN_A_SHORT || ev == BTN_B_SHORT) {
            menu_idx = 0;
            set_state(APP_MENU);
            screen_menu(disp, menu_idx);
        }
        break;

    case APP_MENU:
        if (ev == BTN_A_SHORT) {
            menu_idx = (menu_idx + 1) % 5;
            screen_menu(disp, menu_idx);
        } else if (ev == BTN_B_SHORT) {
            menu_idx = (menu_idx + 4) % 5;  // -1 mod 5
            screen_menu(disp, menu_idx);
        } else if (ev == BTN_A_LONG) {
            // Select
            switch (menu_idx) {
            case 0: set_state(APP_COMPANION);
                    screen_companion(disp, &companion, duat_online); break;
            case 1: set_state(APP_STATS);
                    screen_stats(disp, &companion); break;
            case 2: set_state(APP_BATTLE_MENU);
                    screen_battle_scan(disp, nullptr, 0); break;  // reuse scan screen as menu
            case 3: set_state(APP_SYNC);
                    do_sync(true); break;
            case 4: settings_idx = 0;
                    set_state(APP_SETTINGS);
                    screen_settings(disp, device_cfg.device_id, device_cfg.comp_id, settings_idx); break;
            }
        } else if (ev == BTN_B_LONG) {
            // Back to companion
            set_state(APP_COMPANION);
            screen_companion(disp, &companion, duat_online);
        }
        break;

    case APP_STATS:
        if (ev == BTN_A_LONG || ev == BTN_B_LONG || ev == BTN_B_SHORT) {
            set_state(APP_MENU);
            screen_menu(disp, menu_idx);
        }
        break;

    case APP_BATTLE_MENU:
        // A = scan for opponents, B = advertise (wait for challenge)
        if (ev == BTN_A_SHORT) {
            ble_start_scanning();
            set_state(APP_BATTLE_SCAN);
            screen_battle_scan(disp, nullptr, 0);
        } else if (ev == BTN_B_SHORT) {
            ble_start_advertising();
            set_state(APP_BATTLE_WAIT);
            screen_battle_wait(disp);
        } else if (ev == BTN_A_LONG || ev == BTN_B_LONG) {
            set_state(APP_MENU);
            screen_menu(disp, menu_idx);
        }
        break;

    case APP_BATTLE_SCAN:
        if (ev == BTN_A_SHORT) {
            // Retry scan or challenge first found
            if (ble_found_count > 0) {
                ble_challenge(0);
                if (ble_state == BLE_DONE) {
                    animate_battle();
                } else if (ble_state == BLE_ERROR) {
                    screen_battle_scan(disp, (const char**)scan_names, ble_found_count);
                }
            } else {
                ble_start_scanning();
                screen_battle_scan(disp, nullptr, 0);
            }
        } else if (ev == BTN_B_SHORT) {
            // Advertise instead
            ble_cancel();
            ble_start_advertising();
            set_state(APP_BATTLE_WAIT);
            screen_battle_wait(disp);
        } else if (ev == BTN_A_LONG || ev == BTN_B_LONG) {
            ble_cancel();
            set_state(APP_MENU);
            screen_menu(disp, menu_idx);
        }
        break;

    case APP_BATTLE_WAIT:
        if (ev == BTN_A_SHORT || ev == BTN_A_LONG) {
            ble_cancel();
            set_state(APP_MENU);
            screen_menu(disp, menu_idx);
        }
        break;

    case APP_BATTLE_RESULT:
        if (ev == BTN_A_SHORT || ev == BTN_B_SHORT || ev == BTN_A_LONG) {
            ble_cancel();
            set_state(APP_COMPANION);
            screen_companion(disp, &companion, duat_online);
        }
        break;

    case APP_SYNC:
        // do_sync() blocks; when done it calls set_state(APP_COMPANION)
        // This case handles if user presses back mid-sync (unlikely but safe)
        if (ev == BTN_A_LONG || ev == BTN_B_LONG) {
            set_state(APP_COMPANION);
            screen_companion(disp, &companion, duat_online);
        }
        break;

    case APP_SETTINGS:
        if (ev == BTN_A_SHORT) {
            settings_idx = (settings_idx + 1) % 3;
            screen_settings(disp, device_cfg.device_id, device_cfg.comp_id, settings_idx);
        } else if (ev == BTN_B_SHORT) {
            settings_idx = (settings_idx + 2) % 3;
            screen_settings(disp, device_cfg.device_id, device_cfg.comp_id, settings_idx);
        } else if (ev == BTN_A_LONG) {
            if (settings_idx == 2) {
                // Back
                set_state(APP_MENU);
                screen_menu(disp, menu_idx);
            } else if (settings_idx == 1) {
                // Toggle companion: raven ↔ griffin
                if (strcmp(device_cfg.comp_id, "raven") == 0) {
                    strlcpy(device_cfg.comp_id, "griffin", sizeof(device_cfg.comp_id));
                    companion_default_griffin(&companion);
                } else {
                    strlcpy(device_cfg.comp_id, "raven", sizeof(device_cfg.comp_id));
                    companion_default_raven(&companion);
                }
                device_config_save(&device_cfg);
                ble_init(&companion);
                screen_settings(disp, device_cfg.device_id, device_cfg.comp_id, settings_idx);
            }
        } else if (ev == BTN_B_LONG) {
            set_state(APP_MENU);
            screen_menu(disp, menu_idx);
        }
        break;

    default:
        break;
    }

    delay(10);  // small yield
}
