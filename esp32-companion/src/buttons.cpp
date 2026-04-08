#include "buttons.h"

struct BtnState {
    int      pin;
    bool     active_low;
    bool     pressed;
    uint32_t press_time;
    bool     long_fired;
};

static BtnState btnA = { BTN_A_PIN, true, false, 0, false };
static BtnState btnB = { BTN_B_PIN, true, false, 0, false };

static void init_btn(BtnState* b) {
    pinMode(b->pin, INPUT_PULLUP);
}

static BtnEvent poll_btn(BtnState* b, BtnEvent short_ev, BtnEvent long_ev) {
    bool raw     = digitalRead(b->pin);
    bool pressed = b->active_low ? (raw == LOW) : (raw == HIGH);
    uint32_t now = millis();

    if (pressed && !b->pressed) {
        // falling edge
        b->pressed    = true;
        b->press_time = now;
        b->long_fired = false;
        return BTN_NONE;
    }
    if (pressed && b->pressed && !b->long_fired) {
        if ((now - b->press_time) >= BTN_LONG_MS) {
            b->long_fired = true;
            return long_ev;
        }
    }
    if (!pressed && b->pressed) {
        // rising edge
        uint32_t held = now - b->press_time;
        b->pressed = false;
        if (!b->long_fired && held >= BTN_DEBOUNCE_MS) {
            return short_ev;
        }
    }
    return BTN_NONE;
}

void buttons_init() {
    init_btn(&btnA);
    init_btn(&btnB);
}

BtnEvent buttons_poll() {
    BtnEvent ev;
    ev = poll_btn(&btnA, BTN_A_SHORT, BTN_A_LONG);
    if (ev != BTN_NONE) return ev;
    ev = poll_btn(&btnB, BTN_B_SHORT, BTN_B_LONG);
    return ev;
}
