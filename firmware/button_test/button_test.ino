// Upload this sketch, then press the button.
// One leg to GPIO 25 or GPIO 26. The other leg to GND.
// Serial Monitor at 115200. DOWN means that pin is touching GND.

static const int kPins[] = {25, 26};
static const int kPinCount = 2;

void setup() {
    Serial.begin(115200);
    delay(200);
    for (int i = 0; i < kPinCount; i++) {
        pinMode(kPins[i], INPUT_PULLUP);
    }
    Serial.println("Button test. Press the button.");
}

void loop() {
    for (int i = 0; i < kPinCount; i++) {
        bool down = digitalRead(kPins[i]) == LOW;
        Serial.print(kPins[i]);
        Serial.print(down ? "=DOWN" : "=up");
        if (i + 1 < kPinCount) {
            Serial.print("  ");
        }
    }
    Serial.println();
    delay(200);
}
