#pragma once
#include <Adafruit_GFX.h>
#include "epaper_driver_bsp.h"
#include "config.h"
#include "companion.h"
#include "dungeon.h"

// ── Adafruit GFX wrapper over the Waveshare e-paper driver ───
class CompanionDisplay : public Adafruit_GFX {
public:
    epaper_driver_display* epd;

    CompanionDisplay(epaper_driver_display* d)
        : Adafruit_GFX(EPD_WIDTH, EPD_HEIGHT), epd(d) {}

    void drawPixel(int16_t x, int16_t y, uint16_t color) override {
        if (x < 0 || x >= EPD_WIDTH || y < 0 || y >= EPD_HEIGHT) return;
        epd->EPD_DrawColorPixel(x, y,
            (color == BLACK) ? DRIVER_COLOR_BLACK : DRIVER_COLOR_WHITE);
    }

    void clear()         { epd->EPD_Clear(); fillScreen(WHITE); }
    void refresh_full()  { epd->EPD_Display(); }
    void refresh_part()  { epd->EPD_DisplayPart(); }
    void refresh_base()  { epd->EPD_DisplayPartBaseImage(); }  // full refresh + set 0x26=0x24 for partial follow-ups
    void init_partial()  { epd->EPD_Init_Partial(); }
    void init_full()     { epd->EPD_Init(); }
};

// ── Initialise display hardware ───────────────────────────────
void     display_init(CompanionDisplay* disp);

// ── Global status state (call each loop before any screen draw) ─
void     display_set_status(uint8_t batt_pct, bool dungeon_active);

// ── Status bar (battery % + dungeon flag, drawn at top of screen) ─
void     draw_status_bar(CompanionDisplay* disp, uint8_t batt_pct, bool dungeon_active);

// ── Screen renderers ──────────────────────────────────────────
void     screen_boot(CompanionDisplay* disp, const char* comp_id);
void     screen_companion(CompanionDisplay* disp, const Companion* c, bool duat_online);
void     screen_menu(CompanionDisplay* disp, int selected);
void     screen_stats(CompanionDisplay* disp, const Companion* c);
void     screen_battle_scan(CompanionDisplay* disp, const char** names, int count);
void     screen_battle_wait(CompanionDisplay* disp);
void     screen_battle_fight(CompanionDisplay* disp,
                              const Companion* me, const Companion* opp,
                              int round, int my_hp, int opp_hp,
                              const char* action);
void     screen_battle_result(CompanionDisplay* disp,
                               bool won, int rounds,
                               const char* opp_name);
void     screen_sync(CompanionDisplay* disp, const char* status);
void     screen_settings(CompanionDisplay* disp,
                          const char* device_id, const char* comp_id, int selected);
void     screen_dungeon(CompanionDisplay* disp, const DungeonState* d,
                         const Companion* c, uint8_t batt_pct);
void     screen_dungeon_part(CompanionDisplay* disp, const DungeonState* d,
                              const Companion* c, uint8_t batt_pct);
void     screen_sleep(CompanionDisplay* disp);

// ── Sprite draw functions (used internally + by battle) ───────
void     sprite_raven(CompanionDisplay* disp, int cx, int cy, int scale2);
void     sprite_griffin(CompanionDisplay* disp, int cx, int cy, int scale2);
void     sprite_for(CompanionDisplay* disp, const char* comp_id, int cx, int cy, int scale2);

// ── Helpers ───────────────────────────────────────────────────
void     draw_hp_bar(CompanionDisplay* disp, int x, int y, int w, int h,
                     int hp, int max_hp);
void     draw_centered_text(CompanionDisplay* disp, const char* text,
                             int y, uint8_t size = 1);
