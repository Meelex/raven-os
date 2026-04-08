#include "display.h"
#include <string.h>
#include <stdio.h>

// ── Menu items ───────────────────────────────────────────────
static const char* MENU_ITEMS[] = {
    "COMPANION", "STATS", "DUNGEON", "BATTLE", "SYNC", "SETTINGS"
};
static const int MENU_LEN = 6;

// ── Global status state (set each loop via display_set_status) ─
static uint8_t g_batt_pct      = 100;
static bool    g_dungeon_active = false;

void display_set_status(uint8_t batt, bool dng) {
    g_batt_pct      = batt;
    g_dungeon_active = dng;
}

// ── Init ─────────────────────────────────────────────────────
void display_init(CompanionDisplay* disp) {
    // EPD_PWR_PIN is active-LOW (official Waveshare driver: gpio_set_level(pin, 0) = ON)
    // VBAT_PWR_PIN (GPIO 17) is active-HIGH and powers the main supply rail
    pinMode(17, OUTPUT);
    digitalWrite(17, HIGH);   // VBAT power on (active high)
    delay(10);
    pinMode(EPD_PWR_PIN, OUTPUT);
    digitalWrite(EPD_PWR_PIN, LOW);   // EPD power on (active LOW)
    delay(100);
    disp->epd->EPD_Init();
    disp->epd->EPD_Clear();
    disp->setTextColor(BLACK);
    disp->setTextSize(1);
}

// ── Helpers ───────────────────────────────────────────────────
void draw_centered_text(CompanionDisplay* disp, const char* text, int y, uint8_t size) {
    disp->setTextSize(size);
    int16_t x1, y1;
    uint16_t w, h;
    disp->getTextBounds(text, 0, y, &x1, &y1, &w, &h);
    disp->setCursor((EPD_WIDTH - w) / 2, y);
    disp->print(text);
}

void draw_hp_bar(CompanionDisplay* disp, int x, int y, int w, int h, int hp, int max_hp) {
    disp->drawRect(x, y, w, h, BLACK);
    if (max_hp > 0 && hp > 0) {
        int fill = (int)((long)w * hp / max_hp);
        if (fill > w) fill = w;
        disp->fillRect(x, y, fill, h, BLACK);
    }
}

// ── Sprites ───────────────────────────────────────────────────
// All sprites draw centered at (cx, cy), scale2=10 → 1.0x, scale2=20 → 2.0x

// RAVEN — stylised raven bird (body, wings, tail, head, beak, eye)
void sprite_raven(CompanionDisplay* disp, int cx, int cy, int scale2) {
    auto s = [scale2](int v) { return (int)(v * scale2 / 10); };

    // Body (oval)
    disp->fillEllipse(cx, cy + s(5), s(14), s(10), BLACK);

    // Head
    disp->fillCircle(cx + s(8), cy - s(8), s(7), BLACK);

    // Beak
    disp->fillTriangle(
        cx + s(14), cy - s(9),
        cx + s(22), cy - s(6),
        cx + s(14), cy - s(4),
        BLACK
    );

    // Eye (white dot)
    disp->fillCircle(cx + s(10), cy - s(10), s(2), WHITE);

    // Left wing
    disp->fillTriangle(
        cx - s(4), cy,
        cx - s(22), cy - s(12),
        cx - s(18), cy + s(8),
        BLACK
    );

    // Right wing
    disp->fillTriangle(
        cx + s(4), cy,
        cx + s(20), cy - s(10),
        cx + s(16), cy + s(8),
        BLACK
    );

    // Tail
    disp->fillTriangle(
        cx - s(6), cy + s(14),
        cx + s(6), cy + s(14),
        cx, cy + s(24),
        BLACK
    );

    // Legs
    disp->drawLine(cx - s(4), cy + s(14), cx - s(6), cy + s(22), BLACK);
    disp->drawLine(cx + s(4), cy + s(14), cx + s(6), cy + s(22), BLACK);
    // Talons
    disp->drawLine(cx - s(6), cy + s(22), cx - s(10), cy + s(22), BLACK);
    disp->drawLine(cx + s(6), cy + s(22), cx + s(10), cy + s(22), BLACK);
}

// GRIFFIN — Egyptian griffin: falcon head, lion body, eagle wings
void sprite_griffin(CompanionDisplay* disp, int cx, int cy, int scale2) {
    auto s = [scale2](int v) { return (int)(v * scale2 / 10); };

    // Lion body
    disp->fillEllipse(cx, cy + s(8), s(16), s(12), BLACK);

    // Neck
    disp->fillRect(cx + s(4), cy - s(4), s(8), s(12), BLACK);

    // Falcon head (circle)
    disp->fillCircle(cx + s(10), cy - s(10), s(8), BLACK);

    // Hooked beak
    disp->fillTriangle(
        cx + s(17), cy - s(12),
        cx + s(24), cy - s(8),
        cx + s(17), cy - s(4),
        BLACK
    );

    // Eye (white)
    disp->fillCircle(cx + s(13), cy - s(12), s(2), WHITE);

    // Nemes headdress stripe
    disp->drawLine(cx + s(4), cy - s(4), cx - s(2), cy + s(6), BLACK);
    disp->drawLine(cx + s(6), cy - s(4), cx, cy + s(7), BLACK);

    // Left wing (swept back)
    disp->fillTriangle(
        cx - s(6), cy,
        cx - s(26), cy - s(14),
        cx - s(20), cy + s(10),
        BLACK
    );

    // Right wing (forward)
    disp->fillTriangle(
        cx + s(4), cy,
        cx + s(22), cy - s(10),
        cx + s(18), cy + s(8),
        BLACK
    );

    // Lion tail (curved — approximated with lines)
    disp->drawLine(cx - s(14), cy + s(18), cx - s(20), cy + s(12), BLACK);
    disp->drawLine(cx - s(20), cy + s(12), cx - s(22), cy + s(4), BLACK);
    // Tail tuft
    disp->fillCircle(cx - s(22), cy + s(2), s(3), BLACK);

    // Front paw
    disp->fillRect(cx + s(8), cy + s(18), s(6), s(4), BLACK);
    // Back paw
    disp->fillRect(cx - s(12), cy + s(18), s(6), s(4), BLACK);
}

// SCARAB — Egyptian scarab beetle: round carapace, spread wings, antennae, legs
void sprite_scarab(CompanionDisplay* disp, int cx, int cy, int scale2) {
    auto s = [scale2](int v) { return (int)(v * scale2 / 10); };

    // Left wing (spread)
    disp->fillEllipse(cx - s(14), cy + s(2), s(10), s(14), BLACK);
    // Right wing (spread)
    disp->fillEllipse(cx + s(14), cy + s(2), s(10), s(14), BLACK);

    // Body (round carapace over wings)
    disp->fillCircle(cx, cy + s(4), s(11), BLACK);

    // Head
    disp->fillCircle(cx, cy - s(8), s(6), BLACK);

    // Left antenna
    disp->drawLine(cx - s(2), cy - s(14), cx - s(8), cy - s(22), BLACK);
    disp->fillCircle(cx - s(8), cy - s(22), s(2), BLACK);
    // Right antenna
    disp->drawLine(cx + s(2), cy - s(14), cx + s(8), cy - s(22), BLACK);
    disp->fillCircle(cx + s(8), cy - s(22), s(2), BLACK);

    // Wing division line (center seam on carapace)
    disp->drawLine(cx, cy - s(4), cx, cy + s(14), WHITE);

    // Legs — 3 per side
    disp->drawLine(cx - s(10), cy - s(2), cx - s(20), cy - s(6), BLACK);
    disp->drawLine(cx - s(11), cy + s(4), cx - s(21), cy + s(4), BLACK);
    disp->drawLine(cx - s(10), cy + s(10), cx - s(20), cy + s(14), BLACK);

    disp->drawLine(cx + s(10), cy - s(2), cx + s(20), cy - s(6), BLACK);
    disp->drawLine(cx + s(11), cy + s(4), cx + s(21), cy + s(4), BLACK);
    disp->drawLine(cx + s(10), cy + s(10), cx + s(20), cy + s(14), BLACK);
}

void sprite_for(CompanionDisplay* disp, const char* comp_id, int cx, int cy, int scale2) {
    if (strcmp(comp_id, "griffin") == 0) {
        sprite_griffin(disp, cx, cy, scale2);
    } else if (strcmp(comp_id, "scarab") == 0) {
        sprite_scarab(disp, cx, cy, scale2);
    } else {
        sprite_raven(disp, cx, cy, scale2);
    }
}

// ── Screens ───────────────────────────────────────────────────

void screen_boot(CompanionDisplay* disp, const char* comp_id) {
    // Test pattern: black border + filled center block to verify display pipeline
    disp->epd->EPD_Clear();
    disp->drawRect(0, 0, EPD_WIDTH, EPD_HEIGHT, BLACK);   // full border
    disp->fillRect(10, 10, 180, 180, BLACK);              // large black square
    disp->fillRect(20, 20, 160, 160, WHITE);              // white interior
    disp->drawRect(30, 30, 140, 140, BLACK);              // inner border
    draw_centered_text(disp, "RAVEN OS", 80, 2);
    draw_centered_text(disp, "Companion Device", 115, 1);
    disp->epd->EPD_Display();
}

void screen_companion(CompanionDisplay* disp, const Companion* c, bool duat_online) {
    disp->epd->EPD_Clear();
    draw_status_bar(disp, g_batt_pct, g_dungeon_active);

    // Sprite — centered in upper region (status bar at top 10px, content y=10..128)
    sprite_for(disp, c->id, EPD_WIDTH / 2, 72, 13);

    // Divider
    disp->drawLine(0, 128, EPD_WIDTH, 128, BLACK);

    // Name + level
    char line[40];
    snprintf(line, sizeof(line), "%s  Lv.%d", c->name, c->level);
    draw_centered_text(disp, line, 133, 1);

    // Class
    draw_centered_text(disp, c->class_name, 144, 1);

    // HP bar
    draw_hp_bar(disp, 10, 156, 180, 8, c->hp, c->max_hp);
    snprintf(line, sizeof(line), "HP %d/%d", c->hp, c->max_hp);
    draw_centered_text(disp, line, 167, 1);

    // XP bar (thin)
    int xp_w = (c->xp_to_next > 0)
               ? (int)((long)180 * c->xp / c->xp_to_next) : 0;
    disp->drawRect(10, 179, 180, 4, BLACK);
    if (xp_w > 0) disp->fillRect(10, 179, xp_w, 4, BLACK);

    // Duat online indicator (small dot bottom-left)
    if (duat_online) disp->fillCircle(5, EPD_HEIGHT - 5, 3, BLACK);

    disp->epd->EPD_Display();
}

void screen_menu(CompanionDisplay* disp, int selected) {
    disp->epd->EPD_Clear();

    disp->setTextSize(1);
    disp->setTextColor(BLACK);
    disp->setCursor(4, 4);
    disp->print("MENU");
    disp->drawLine(0, 16, EPD_WIDTH, 16, BLACK);

    int item_h = 30;  // 6 items × 30px = 180px, fits in 200-18=182
    for (int i = 0; i < MENU_LEN; i++) {
        int y = 18 + i * item_h;
        if (i == selected) {
            disp->fillRect(0, y, EPD_WIDTH, item_h - 2, BLACK);
            disp->setTextColor(WHITE);
        } else {
            disp->setTextColor(BLACK);
        }
        disp->setCursor(12, y + 10);
        disp->setTextSize(1);
        disp->print(MENU_ITEMS[i]);
    }
    disp->setTextColor(BLACK);

    disp->epd->EPD_Display();
}

void screen_stats(CompanionDisplay* disp, const Companion* c) {
    disp->epd->EPD_Clear();
    draw_status_bar(disp, g_batt_pct, g_dungeon_active);

    draw_centered_text(disp, "STATS", STATUS_BAR_H + 4, 1);
    disp->drawLine(0, STATUS_BAR_H + 14, EPD_WIDTH, STATUS_BAR_H + 14, BLACK);

    char line[48];
    snprintf(line, sizeof(line), "%s  (%s)", c->name, c->class_name);
    draw_centered_text(disp, line, STATUS_BAR_H + 20, 1);

    int oy = STATUS_BAR_H + 10;  // offset for remaining rows
    snprintf(line, sizeof(line), "Level: %d", c->level);
    disp->setCursor(10, oy + 38); disp->setTextSize(1); disp->print(line);

    snprintf(line, sizeof(line), "HP:  %d / %d", c->hp, c->max_hp);
    disp->setCursor(10, oy + 52); disp->print(line);

    snprintf(line, sizeof(line), "ATK: %d", c->attack);
    disp->setCursor(10, oy + 66); disp->print(line);

    snprintf(line, sizeof(line), "DEF: %d", c->defense);
    disp->setCursor(10, oy + 80); disp->print(line);

    snprintf(line, sizeof(line), "XP:  %d / %d", c->xp, c->xp_to_next);
    disp->setCursor(10, oy + 94); disp->print(line);

    disp->drawLine(0, oy + 108, EPD_WIDTH, oy + 108, BLACK);

    snprintf(line, sizeof(line), "Wins:   %d", c->wins);
    disp->setCursor(10, oy + 116); disp->print(line);

    snprintf(line, sizeof(line), "Losses: %d", c->losses);
    disp->setCursor(10, oy + 130); disp->print(line);

    disp->epd->EPD_Display();
}

void screen_battle_scan(CompanionDisplay* disp, const char** names, int count) {
    disp->epd->EPD_Clear();

    draw_centered_text(disp, "BATTLE", 4, 1);
    disp->drawLine(0, 14, EPD_WIDTH, 14, BLACK);
    draw_centered_text(disp, "Scanning...", 24, 1);

    if (count == 0) {
        draw_centered_text(disp, "No opponents found", 60, 1);
        draw_centered_text(disp, "A=retry  B=wait", 80, 1);
    } else {
        for (int i = 0; i < count && i < 4; i++) {
            char line[32];
            snprintf(line, sizeof(line), "> %s", names[i]);
            disp->setCursor(10, 48 + i * 18);
            disp->setTextSize(1);
            disp->print(line);
        }
        draw_centered_text(disp, "A=challenge  B=back", 160, 1);
    }

    disp->epd->EPD_Display();
}

void screen_battle_wait(CompanionDisplay* disp) {
    disp->epd->EPD_Clear();

    draw_centered_text(disp, "BATTLE", 4, 1);
    disp->drawLine(0, 14, EPD_WIDTH, 14, BLACK);
    draw_centered_text(disp, "Waiting for", 40, 1);
    draw_centered_text(disp, "opponent...", 56, 1);

    // Animated ellipsis suggestion (static: dots)
    draw_centered_text(disp, "....", 80, 2);
    draw_centered_text(disp, "A=cancel", 160, 1);

    disp->epd->EPD_Display();
}

void screen_battle_fight(CompanionDisplay* disp,
                          const Companion* me, const Companion* opp,
                          int round, int my_hp, int opp_hp,
                          const char* action)
{
    disp->epd->EPD_Clear();

    // Player sprite (left)
    sprite_for(disp, me->id, 48, 70, 9);

    // Opponent sprite (right, mirrored via offset)
    sprite_for(disp, opp->id, EPD_WIDTH - 48, 70, 9);

    // VS divider
    disp->drawLine(EPD_WIDTH / 2, 20, EPD_WIDTH / 2, 120, BLACK);
    disp->setCursor(EPD_WIDTH / 2 - 5, 55);
    disp->setTextSize(1);
    disp->print("VS");

    char line[32];
    snprintf(line, sizeof(line), "Round %d", round);
    draw_centered_text(disp, line, 4, 1);

    disp->drawLine(0, 122, EPD_WIDTH, 122, BLACK);

    // HP bars
    draw_hp_bar(disp, 4,  128, 90, 6, my_hp,  me->max_hp);
    draw_hp_bar(disp, 106, 128, 90, 6, opp_hp, opp->max_hp);

    snprintf(line, sizeof(line), "%d/%d", my_hp, me->max_hp);
    disp->setCursor(4, 137); disp->setTextSize(1); disp->print(line);

    snprintf(line, sizeof(line), "%d/%d", opp_hp, opp->max_hp);
    disp->setCursor(106, 137); disp->print(line);

    // Action text
    draw_centered_text(disp, action, 155, 1);

    disp->epd->EPD_DisplayPart();
}

void screen_battle_result(CompanionDisplay* disp,
                           bool won, int rounds,
                           const char* opp_name)
{
    disp->epd->EPD_Clear();

    draw_centered_text(disp, won ? "VICTORY!" : "DEFEAT", 20, 2);

    disp->drawLine(0, 44, EPD_WIDTH, 44, BLACK);

    char line[48];
    snprintf(line, sizeof(line), "vs %s", opp_name);
    draw_centered_text(disp, line, 54, 1);

    snprintf(line, sizeof(line), "%d rounds", rounds);
    draw_centered_text(disp, line, 68, 1);

    draw_centered_text(disp, won ? "XP gained!" : "HP reduced", 90, 1);
    draw_centered_text(disp, "Result queued for sync", 110, 1);
    draw_centered_text(disp, "A=continue", 160, 1);

    disp->epd->EPD_Display();
}

void screen_sync(CompanionDisplay* disp, const char* status) {
    disp->epd->EPD_Clear();

    draw_centered_text(disp, "SYNC", 4, 1);
    disp->drawLine(0, 14, EPD_WIDTH, 14, BLACK);
    draw_centered_text(disp, status, 60, 1);

    disp->epd->EPD_Display();
}

void screen_settings(CompanionDisplay* disp,
                      const char* device_id, const char* comp_id, int selected)
{
    disp->epd->EPD_Clear();

    draw_centered_text(disp, "SETTINGS", 4, 1);
    disp->drawLine(0, 14, EPD_WIDTH, 14, BLACK);

    const char* items[] = { "Device ID", "Companion", "Back" };
    const char* vals[]  = { device_id, comp_id, "" };
    for (int i = 0; i < 3; i++) {
        int y = 24 + i * 40;
        if (i == selected) {
            disp->fillRect(0, y, EPD_WIDTH, 38, BLACK);
            disp->setTextColor(WHITE);
        } else {
            disp->setTextColor(BLACK);
        }
        disp->setCursor(8, y + 6);
        disp->setTextSize(1);
        disp->print(items[i]);
        disp->setCursor(8, y + 20);
        disp->print(vals[i]);
    }
    disp->setTextColor(BLACK);

    disp->epd->EPD_Display();
}

// ── Status bar ────────────────────────────────────────────────
// Draws battery icon + % top-right, dungeon "DNG" top-left (if active)
void draw_status_bar(CompanionDisplay* disp, uint8_t batt_pct, bool dungeon_active) {
    // Battery icon (13x7) at top-right
    int bx = EPD_WIDTH - 17;
    int by = 1;
    disp->drawRect(bx, by, 12, 7, BLACK);
    disp->fillRect(bx + 12, by + 2, 2, 3, BLACK);  // nub
    int fill = (batt_pct * 10) / 100;
    if (fill > 0) disp->fillRect(bx + 1, by + 1, fill, 5, BLACK);

    char pct[6];
    snprintf(pct, sizeof(pct), "%d%%", batt_pct);
    disp->setTextSize(1);
    disp->setTextColor(BLACK);
    int16_t x1, y1; uint16_t tw, th;
    disp->getTextBounds(pct, 0, 0, &x1, &y1, &tw, &th);
    disp->setCursor(bx - tw - 3, by);
    disp->print(pct);

    if (dungeon_active) {
        disp->setCursor(2, by);
        disp->print("DNG");
    }
    // Separator line
    disp->drawLine(0, STATUS_BAR_H, EPD_WIDTH, STATUS_BAR_H, BLACK);
}

// ── Dungeon screen ────────────────────────────────────────────
void screen_dungeon(CompanionDisplay* disp, const DungeonState* d,
                    const Companion* c, uint8_t batt_pct)
{
    disp->epd->EPD_Clear();
    draw_status_bar(disp, batt_pct, true);

    int y = STATUS_BAR_H + 2;

    if (d->sub == DSUB_COMBAT || d->sub == DSUB_LEVELUP || d->sub == DSUB_LOOT) {
        // ── COMBAT / REWARD view ──────────────────────────────
        const DungeonEnemy& e = d->enemy;
        bool in_combat = (d->sub == DSUB_COMBAT);

        // Header: enemy name
        char hdr[32];
        snprintf(hdr, sizeof(hdr), in_combat ? "COMBAT: %s" : "%s DEFEATED",
                 e.name[0] ? e.name : "???");
        draw_centered_text(disp, hdr, y, 1);
        y += 12;
        disp->drawLine(0, y, EPD_WIDTH, y, BLACK);
        y += 3;

        // Enemy HP bar (or blank if dead)
        if (in_combat) {
            draw_hp_bar(disp, 4, y, EPD_WIDTH - 8, 7,
                        max(0, e.hp), e.max_hp);
            char ehp[20];
            snprintf(ehp, sizeof(ehp), "%s %d/%d", e.name, max(0,e.hp), e.max_hp);
            disp->setCursor(4, y + 9); disp->setTextSize(1); disp->print(ehp);
        }
        y += 20;

        // Player HP bar
        draw_hp_bar(disp, 4, y, EPD_WIDTH - 8, 7, c->hp, c->max_hp);
        char php[24];
        snprintf(php, sizeof(php), "%s HP %d/%d", c->name, c->hp, c->max_hp);
        disp->setCursor(4, y + 9); disp->setTextSize(1); disp->print(php);
        y += 22;

        disp->drawLine(0, y, EPD_WIDTH, y, BLACK);
        y += 3;

        // Log lines
        for (int i = 0; i < DLOG_LEN; i++) {
            if (d->log[i][0]) {
                disp->setCursor(4, y);
                disp->setTextSize(1);
                disp->print(d->log[i]);
                y += 12;
            }
        }

        // Loot message if applicable
        if (!in_combat && d->loot_msg[0]) {
            disp->setCursor(4, y);
            disp->print(d->loot_msg);
        }

    } else if (d->sub == DSUB_DEAD) {
        // ── DEAD view ─────────────────────────────────────────
        draw_centered_text(disp, "** FALLEN **", y + 10, 1);
        y += 30;
        draw_centered_text(disp, "FALLEN IN THE WASTES", y, 1);
        y += 16;
        draw_centered_text(disp, "Respawning...", y, 1);
        y += 16;
        // HP bar empty
        draw_hp_bar(disp, 20, y, EPD_WIDTH - 40, 8, 0, c->max_hp);

    } else {
        // ── EXPLORE view ──────────────────────────────────────
        // Area name header
        draw_centered_text(disp, DUNGEON_AREAS[d->area], y, 1);
        y += 12;
        disp->drawLine(0, y, EPD_WIDTH, y, BLACK);
        y += 4;

        // Player HP bar
        draw_hp_bar(disp, 4, y, EPD_WIDTH - 8, 7, c->hp, c->max_hp);
        char php[24];
        snprintf(php, sizeof(php), "HP %d/%d  Gold:%d", c->hp, c->max_hp, d->gold);
        disp->setCursor(4, y + 9); disp->setTextSize(1); disp->print(php);
        y += 22;

        disp->drawLine(0, y, EPD_WIDTH, y, BLACK);
        y += 4;

        // Log lines
        for (int i = 0; i < DLOG_LEN; i++) {
            if (d->log[i][0]) {
                disp->setCursor(4, y);
                disp->setTextSize(1);
                disp->print(d->log[i]);
                y += 12;
            }
        }
    }

    // Stats footer
    char stats[32];
    snprintf(stats, sizeof(stats), "Lv%d  %dW/%dL  XP:%d/%d",
             c->level, c->wins, c->losses, c->xp, c->xp_to_next);
    disp->setCursor(2, EPD_HEIGHT - 20);
    disp->setTextSize(1);
    disp->print(stats);

    draw_centered_text(disp, "A/B=back", EPD_HEIGHT - 9, 1);
    disp->epd->EPD_Display();
}

// ── Dungeon combat partial refresh ────────────────────────────
// Call instead of screen_dungeon during active combat for speed
void screen_dungeon_part(CompanionDisplay* disp, const DungeonState* d,
                         const Companion* c, uint8_t batt_pct)
{
    disp->epd->EPD_Clear();
    draw_status_bar(disp, batt_pct, true);

    int y = STATUS_BAR_H + 2;
    const DungeonEnemy& e = d->enemy;

    char hdr[32];
    snprintf(hdr, sizeof(hdr), "COMBAT: %s", e.name);
    draw_centered_text(disp, hdr, y, 1);
    y += 12;
    disp->drawLine(0, y, EPD_WIDTH, y, BLACK);
    y += 3;

    draw_hp_bar(disp, 4, y, EPD_WIDTH - 8, 7, max(0, e.hp), e.max_hp);
    char ehp[20];
    snprintf(ehp, sizeof(ehp), "%s %d/%d", e.name, max(0, e.hp), e.max_hp);
    disp->setCursor(4, y + 9); disp->setTextSize(1); disp->print(ehp);
    y += 20;

    draw_hp_bar(disp, 4, y, EPD_WIDTH - 8, 7, c->hp, c->max_hp);
    char php[24];
    snprintf(php, sizeof(php), "%s %d/%d", c->name, c->hp, c->max_hp);
    disp->setCursor(4, y + 9); disp->setTextSize(1); disp->print(php);
    y += 22;

    disp->drawLine(0, y, EPD_WIDTH, y, BLACK);
    y += 3;

    for (int i = 0; i < DLOG_LEN; i++) {
        if (d->log[i][0]) {
            disp->setCursor(4, y);
            disp->setTextSize(1);
            disp->print(d->log[i]);
            y += 12;
        }
    }

    disp->epd->EPD_DisplayPart();
}

// ── Sleep screen ──────────────────────────────────────────────
void screen_sleep(CompanionDisplay* disp) {
    disp->epd->EPD_Clear();
    draw_centered_text(disp, "RAVEN OS", 70, 2);
    draw_centered_text(disp, "Sleeping...", 108, 1);
    draw_centered_text(disp, "Press any button to wake", 140, 1);
    disp->epd->EPD_Display();
}
