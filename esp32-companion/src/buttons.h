#pragma once
#include <Arduino.h>
#include "config.h"

enum BtnEvent {
    BTN_NONE,
    BTN_A_SHORT,    // cycle forward / confirm
    BTN_A_LONG,     // back / cancel
    BTN_B_SHORT,    // cycle back / secondary
    BTN_B_LONG,     // context action
};

void     buttons_init();
BtnEvent buttons_poll();   // call every loop() — returns event or BTN_NONE
