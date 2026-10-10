/*
 * andrew.ino
 * NU-40 DK + Qwiic 6-axis IMU firmware
 *
 * 목적
 *   - NU IMU LSM6DS3TR 6축 IMU의 가속도/각속도 수집
 *   - USB CDC로 PC에 한 줄 CSV 패킷 전송
 *   - 두 NU40 보드를 같은 스케치로 사용하면서 왼손/오른손 구분
 *   - 초기 정지 상태에서 자이로 바이어스 자동 보정
 *
 * NU-40 DK Arduino 설정
 *   1) 보드 매니저 URL:
 *      https://raw.githubusercontent.com/Nucode01/Adafruit_nRF52_Arduino/refs/heads/master/package_nuduino_index.json
 *   2) Board: NUBoards nRF52 -> NU40DK nRF52840
 *   3) Qwiic 센서를 연결하고 업로드
 *
 * 손 구분
 *   - 보드별로 DEFAULT_HAND_SIDE를 다르게 설정하여 고정
 *   - 왼손 보드: DEFAULT_HAND_SIDE HAND_LEFT
 *   - 오른손 보드: DEFAULT_HAND_SIDE HAND_RIGHT
 *   - 버튼으로 손 구분을 변경하지 않음
 *
 * 버튼
 *   - BUTTON1(P0.11) 짧게 누름: 다음 모드(raw -> motion -> shortcut)
 *   - BUTTON2(P0.12) 짧게 누름: 이전 모드
 *   - BUTTON1 1.5초 이상 길게 누름: 현재 모드에서 잡고 있는 자세를 6축 영점으로 저장
 *   - BUTTON2 1.5초 이상 길게 누름: 현재 모드의 6축 영점 삭제
 *   - 모드 값은 PC 패킷의 mode 필드에도 함께 전송
 *   - OLED 표시 기능은 추후 연결 시 updateModeDisplay()에 추가
 *
 * 단축키 모드 이스터 에그
 *   - MODE_SHORTCUT에서 저장된 영점 기준으로 위->아래 왕복을 센다.
 *   - 한 보드에서 10회가 되면 updown_count를 PC로 전송한다.
 *   - PC가 좌우 보드 모두 10회 이상임을 확인하면 두 보드에 EASTER=ON을 전송한다.
 *   - EASTER=ON을 받은 보드는 LED 글리치 패턴을 실행하고 OLED 훅을 호출한다.
 *
 * PC 전송 형식
 *   JSON Lines: JSON 객체 1개를 한 줄로 전송
 *   type=imu, mode_id, mode_state, calibration, imu, zero_valid 포함
 *
 * PC 단축키 설정 예정값
 *   아래 값은 현재 PC 프로그램이 없으므로 주석으로만 보관합니다.
 *   PC 프로그램 구현 시 이 값을 옮겨서 수정하면 됩니다.
 *
 *   // 활성화 키: Ctrl + Q + E
 *   // const char* IMU_ARM_HOTKEY = "CTRL+Q+E";
 *   // const uint32_t IMU_ARM_DELAY_MS = 300;
 *
 *   // 두 번째 순서 키: Ctrl + Tab + Q
 *   // const char* IMU_SECOND_HOTKEY = "CTRL+TAB+Q";
 *
 *   // 단축키별 실행 후 대기 시간
 *   // const uint32_t SAVE_COOLDOWN_MS = 300;
 *   // const uint32_t UNDO_COOLDOWN_MS = 700;
 *   // const uint32_t CAPTURE_COOLDOWN_MS = 1000;
 *
 * 필드
 *   schema  : JSON 데이터 구조 버전(현재 1)
 *   hand    : L=왼손, R=오른손
 *   seq     : 보드별 증가 시퀀스
 *   time_ms : millis() 기준 보드 시간
 *   a       : 가속도, g 단위(중력 포함)
 *   g       : 각속도, deg/s 단위(자이로 바이어스 보정 후)
 *   calibration : not_started, calibrating, ready, error
 *   mode_id     : raw, motion, shortcut 또는 사용자 정의 식별자
 *   zero_valid : 현재 모드의 6축 영점 저장 여부
 *   rel_*   : 현재 모드 영점을 뺀 상대 센서값. 영점이 없으면 원본값과 동일
 *   updown_count : 단축키 모드에서 감지한 위->아래 왕복 횟수
 *   easter  : 0=일반, 1=LED/OLED 글리치 실행 중
 *   imu     : 자동 감지된 센서 이름
 *
 * 주의
 *   이 펌웨어는 자세/제스처의 의미를 판단하지 않습니다.
 *   기울기, 회전, 제스처 및 키보드/마우스 변환은 PC 프로그램에서 처리합니다.
 *
 * 센서
 *   NU IMU LSM6DS3TR. LSM6DS3TR-C와 같은 레지스터 맵을 사용합니다.
 */

#include <Arduino.h>
#include <Wire.h>

// -----------------------------------------------------------------------------
// 사용자 설정
// -----------------------------------------------------------------------------

enum HandSide : uint8_t {
  HAND_LEFT  = 0,
  HAND_RIGHT = 1
};

enum InputMode : uint8_t {
  MODE_RAW      = 0,
  MODE_MOTION   = 1,
  MODE_SHORTCUT = 2
};

// 왼손 보드는 HAND_LEFT, 오른손 보드는 HAND_RIGHT로 각각 설정해 업로드합니다.
#define DEFAULT_HAND_SIDE HAND_LEFT

// PC가 모드 변환을 담당하므로 초기값은 raw입니다.
#define DEFAULT_INPUT_MODE MODE_RAW

static const uint8_t INPUT_MODE_COUNT = 3;

// 모드별 자세 영점. 전원 재인가 후에는 다시 설정해야 합니다.
// 가속도는 g, 각속도는 자이로 바이어스 보정 후 deg/s입니다.
struct ModeZero {
  float ax;
  float ay;
  float az;
  float gx;
  float gy;
  float gz;
  bool valid;
};

// 전원 인가 후 센서를 움직이지 않고 유지하는 시간
static const uint32_t CALIBRATION_TIME_MS = 3000;

// USB로 보낼 샘플 주기. 100 Hz
static const uint32_t SAMPLE_PERIOD_US = 10000;

// 길게 누름 판정 시간
static const uint32_t BUTTON_LONG_PRESS_MS = 1500;

// 이스터 에그 동작 감도와 지속 시간
// NU IMU를 잡았을 때 위/아래 움직임이 나타나는 축. 0=X, 1=Y, 2=Z
static const uint8_t VERTICAL_ACCEL_AXIS = 1;
static const float VERTICAL_GESTURE_THRESHOLD_G = 0.35f;
static const uint32_t EASTER_EGG_DURATION_MS = 6000;
static const uint8_t EASTER_EGG_CYCLE_THRESHOLD = 10;

// LSM6DS 계열을 104 Hz, 가속도 +/-2 g, 자이로 245 dps로 설정
static const float ACCEL_G_PER_LSB = 0.000061f;
static const float GYRO_DPS_PER_LSB = 0.00875f;

// LSM6DS 계열 공통 레지스터
static const uint8_t REG_WHO_AM_I  = 0x0F;
static const uint8_t REG_CTRL1_XL  = 0x10;
static const uint8_t REG_CTRL2_G   = 0x11;
static const uint8_t REG_CTRL3_C   = 0x12;
static const uint8_t REG_OUTX_L_G  = 0x22;
static const uint8_t REG_OUTX_L_XL = 0x28;

#if defined(PIN_BUTTON1)
static const uint8_t LEFT_BUTTON_PIN = PIN_BUTTON1;
#else
static const uint8_t LEFT_BUTTON_PIN = 11;
#endif

#if defined(PIN_BUTTON2)
static const uint8_t RIGHT_BUTTON_PIN = PIN_BUTTON2;
#else
static const uint8_t RIGHT_BUTTON_PIN = 12;
#endif

#if defined(LED_STATE_ON)
static const uint8_t LED_ON_LEVEL = LED_STATE_ON;
#else
static const uint8_t LED_ON_LEVEL = HIGH;
#endif

static const uint8_t LED_OFF_LEVEL = (LED_ON_LEVEL == HIGH) ? LOW : HIGH;

// -----------------------------------------------------------------------------
// 자료 구조
// -----------------------------------------------------------------------------

struct ImuSample {
  float ax;
  float ay;
  float az;
  float gx;
  float gy;
  float gz;
};

enum CalibrationState : uint8_t {
  CAL_NOT_STARTED = 0,
  CALIBRATING     = 1,
  CAL_READY       = 2,
  CAL_ERROR       = 3
};

enum VerticalGestureState : uint8_t {
  VERTICAL_NEUTRAL = 0,
  VERTICAL_UP      = 1,
  VERTICAL_DOWN    = 2
};

enum ImuKind : uint8_t {
  IMU_NONE = 0,
  IMU_LSM6DSX
};

// -----------------------------------------------------------------------------
// LSM6DS3TR 최소 드라이버
// -----------------------------------------------------------------------------

class Lsm6ds3tr {
 public:
  bool begin(TwoWire& bus) {
    _bus = &bus;
    _address = 0;
    _whoAmI = 0;

    // LSM6DS3TR의 일반적인 7-bit 주소
    const uint8_t addresses[] = {0x6A, 0x6B};
    for (uint8_t i = 0; i < sizeof(addresses); ++i) {
      uint8_t who = 0;
      if (!readRegisterAt(addresses[i], REG_WHO_AM_I, who)) {
        continue;
      }

      // LSM6DS3TR 계열: 제품 버전에 따라 0x69 또는 0x6A
      if (isLsm6ds3trWhoAmI(who)) {
        _address = addresses[i];
        _whoAmI = who;
        break;
      }
    }

    if (_address == 0) {
      return false;
    }

    // BDU=1, IF_INC=1: 데이터 일관성과 연속 레지스터 읽기 활성화
    if (!writeRegister(REG_CTRL3_C, 0x44)) {
      return false;
    }

    // ODR=104 Hz, FS_XL=+/-2 g
    if (!writeRegister(REG_CTRL1_XL, 0x40)) {
      return false;
    }

    // ODR=104 Hz, FS_G=245 dps
    if (!writeRegister(REG_CTRL2_G, 0x40)) {
      return false;
    }

    delay(20);
    return true;
  }

  bool read(ImuSample& sample) {
    if (_bus == nullptr || _address == 0) {
      return false;
    }

    uint8_t gyroBytes[6];
    uint8_t accelBytes[6];
    if (!readRegisters(REG_OUTX_L_G, gyroBytes, sizeof(gyroBytes))) {
      return false;
    }
    if (!readRegisters(REG_OUTX_L_XL, accelBytes, sizeof(accelBytes))) {
      return false;
    }

    const int16_t gxRaw = makeInt16(gyroBytes[0], gyroBytes[1]);
    const int16_t gyRaw = makeInt16(gyroBytes[2], gyroBytes[3]);
    const int16_t gzRaw = makeInt16(gyroBytes[4], gyroBytes[5]);
    const int16_t axRaw = makeInt16(accelBytes[0], accelBytes[1]);
    const int16_t ayRaw = makeInt16(accelBytes[2], accelBytes[3]);
    const int16_t azRaw = makeInt16(accelBytes[4], accelBytes[5]);

    sample.ax = static_cast<float>(axRaw) * ACCEL_G_PER_LSB;
    sample.ay = static_cast<float>(ayRaw) * ACCEL_G_PER_LSB;
    sample.az = static_cast<float>(azRaw) * ACCEL_G_PER_LSB;
    sample.gx = static_cast<float>(gxRaw) * GYRO_DPS_PER_LSB;
    sample.gy = static_cast<float>(gyRaw) * GYRO_DPS_PER_LSB;
    sample.gz = static_cast<float>(gzRaw) * GYRO_DPS_PER_LSB;
    return true;
  }

  const char* name() const {
    return (_address == 0) ? "NONE" : "LSM6DS3TR";
  }

  uint8_t address() const {
    return _address;
  }

  uint8_t whoAmI() const {
    return _whoAmI;
  }

 private:
  TwoWire* _bus = nullptr;
  uint8_t _address = 0;
  uint8_t _whoAmI = 0;

  static bool isLsm6ds3trWhoAmI(uint8_t who) {
    switch (who) {
      case 0x69:  // LSM6DS3TR
      case 0x6A:  // LSM6DS3TR-C
        return true;
      default:
        return false;
    }
  }

  static int16_t makeInt16(uint8_t low, uint8_t high) {
    return static_cast<int16_t>(static_cast<uint16_t>(low) |
                                (static_cast<uint16_t>(high) << 8));
  }

  bool writeRegister(uint8_t reg, uint8_t value) {
    _bus->beginTransmission(_address);
    _bus->write(reg);
    _bus->write(value);
    return _bus->endTransmission() == 0;
  }

  bool readRegisterAt(uint8_t address, uint8_t reg, uint8_t& value) {
    _bus->beginTransmission(address);
    _bus->write(reg);
    if (_bus->endTransmission(false) != 0) {
      return false;
    }

    const uint8_t received = _bus->requestFrom(address, static_cast<uint8_t>(1));
    if (received != 1 || !_bus->available()) {
      return false;
    }
    value = _bus->read();
    return true;
  }

  bool readRegisters(uint8_t startReg, uint8_t* buffer, uint8_t length) {
    _bus->beginTransmission(_address);
    _bus->write(startReg);
    if (_bus->endTransmission(false) != 0) {
      return false;
    }

    const uint8_t received = _bus->requestFrom(_address, length);
    if (received != length) {
      while (_bus->available()) {
        _bus->read();
      }
      return false;
    }

    for (uint8_t i = 0; i < length; ++i) {
      buffer[i] = _bus->read();
    }
    return true;
  }
};

// -----------------------------------------------------------------------------
// 전역 상태
// -----------------------------------------------------------------------------

Lsm6ds3tr imu;
HandSide handSide = DEFAULT_HAND_SIDE;
InputMode inputMode = DEFAULT_INPUT_MODE;
CalibrationState calibrationState = CAL_NOT_STARTED;
ModeZero modeZeros[INPUT_MODE_COUNT] = {};
VerticalGestureState verticalGestureState = VERTICAL_NEUTRAL;
uint8_t upDownCount = 0;
bool easterEggActive = false;
uint32_t easterEggEndMs = 0;
uint16_t easterEggLedStep = 0;

float gyroBiasX = 0.0f;
float gyroBiasY = 0.0f;
float gyroBiasZ = 0.0f;
float gyroSumX = 0.0f;
float gyroSumY = 0.0f;
float gyroSumZ = 0.0f;
uint32_t calibrationStartMs = 0;
uint32_t calibrationSamples = 0;
uint32_t sequenceNumber = 0;
uint32_t nextSampleUs = 0;
uint32_t lastErrorReportMs = 0;
uint32_t lastLedToggleMs = 0;
bool ledState = false;
String commandBuffer;

struct ButtonState {
  bool lastReading;
  bool stablePressed;
  bool longPressHandled;
  uint32_t lastChangeMs;
  uint32_t pressStartMs;
};

ButtonState nextModeButton = {false, false, false, 0, 0};
ButtonState previousModeButton = {false, false, false, 0, 0};
bool zeroCaptureRequested = false;

void resetVerticalGesture();

// -----------------------------------------------------------------------------
// 출력 및 상태 처리
// -----------------------------------------------------------------------------

char handCharacter() {
  return (handSide == HAND_LEFT) ? 'L' : 'R';
}

const char* boardName() {
  return (handSide == HAND_LEFT) ? "LEFT" : "RIGHT";
}

const char* modeIdString() {
  switch (inputMode) {
    case MODE_MOTION:
      return "motion";
    case MODE_SHORTCUT:
      return "shortcut";
    case MODE_RAW:
    default:
      return "raw";
  }
}

const char* calibrationString() {
  switch (calibrationState) {
    case CALIBRATING:
      return "calibrating";
    case CAL_READY:
      return "ready";
    case CAL_ERROR:
      return "error";
    case CAL_NOT_STARTED:
    default:
      return "not_started";
  }
}

void setLed(bool on) {
  digitalWrite(LED_BUILTIN, on ? LED_ON_LEVEL : LED_OFF_LEVEL);
  ledState = on;
}

void printComment(const char* message) {
  Serial.print(F("{\"type\":\"event\",\"schema\":1,\"event\":\""));
  Serial.print(message);
  Serial.print(F("\",\"board\":\""));
  Serial.print(boardName());
  Serial.println(F("\"}"));
}

void emitCalibrationEvent(const char* status) {
  Serial.print(F("{\"type\":\"calibration\",\"schema\":1,\"board\":\""));
  Serial.print(boardName());
  Serial.print(F("\",\"status\":\""));
  Serial.print(status);
  Serial.println(F("\"}"));
}

void emitSample(const ImuSample& sample) {
  const float correctedGx = sample.gx - gyroBiasX;
  const float correctedGy = sample.gy - gyroBiasY;
  const float correctedGz = sample.gz - gyroBiasZ;

  const ModeZero& zero = modeZeros[static_cast<uint8_t>(inputMode)];
  const float relativeAx = zero.valid ? sample.ax - zero.ax : sample.ax;
  const float relativeAy = zero.valid ? sample.ay - zero.ay : sample.ay;
  const float relativeAz = zero.valid ? sample.az - zero.az : sample.az;
  const float relativeGx = zero.valid ? correctedGx - zero.gx : correctedGx;
  const float relativeGy = zero.valid ? correctedGy - zero.gy : correctedGy;
  const float relativeGz = zero.valid ? correctedGz - zero.gz : correctedGz;

  Serial.print(F("{\"type\":\"imu\",\"schema\":1,\"device_id\":\"nu40-"));
  Serial.print((handSide == HAND_LEFT) ? F("left") : F("right"));
  Serial.print(F("\",\"hand\":\""));
  Serial.print(handCharacter());
  Serial.print(F("\",\"seq\":"));
  Serial.print(sequenceNumber++);
  Serial.print(F(",\"ts_ms\":"));
  Serial.print(millis());
  Serial.print(F(",\"mode_id\":\""));
  Serial.print(modeIdString());
  Serial.print(F("\",\"mode_state\":\"active\",\"calibration\":\""));
  Serial.print(calibrationString());
  Serial.print(F("\",\"zero_valid\":"));
  Serial.print(zero.valid ? F("true") : F("false"));
  Serial.print(F(",\"updown_count\":"));
  Serial.print(upDownCount);
  Serial.print(F(",\"easter\":"));
  Serial.print(easterEggActive ? F("true") : F("false"));
  Serial.print(F(",\"imu\":{\"name\":\""));
  Serial.print(imu.name());
  Serial.print(F("\",\"ax_g\":"));
  Serial.print(sample.ax, 5);
  Serial.print(F(",\"ay_g\":"));
  Serial.print(sample.ay, 5);
  Serial.print(F(",\"az_g\":"));
  Serial.print(sample.az, 5);
  Serial.print(F(",\"gx_dps\":"));
  Serial.print(correctedGx, 5);
  Serial.print(F(",\"gy_dps\":"));
  Serial.print(correctedGy, 5);
  Serial.print(F(",\"gz_dps\":"));
  Serial.print(correctedGz, 5);
  Serial.print(F(",\"rel_ax_g\":"));
  Serial.print(relativeAx, 5);
  Serial.print(F(",\"rel_ay_g\":"));
  Serial.print(relativeAy, 5);
  Serial.print(F(",\"rel_az_g\":"));
  Serial.print(relativeAz, 5);
  Serial.print(F(",\"rel_gx_dps\":"));
  Serial.print(relativeGx, 5);
  Serial.print(F(",\"rel_gy_dps\":"));
  Serial.print(relativeGy, 5);
  Serial.print(F(",\"rel_gz_dps\":"));
  Serial.print(relativeGz, 5);
  Serial.println(F("}}"));
}

void emitSensorError() {
  Serial.print(F("{\"type\":\"error\",\"schema\":1,\"device_id\":\"nu40-"));
  Serial.print((handSide == HAND_LEFT) ? F("left") : F("right"));
  Serial.print(F("\",\"hand\":\""));
  Serial.print(handCharacter());
  Serial.print(F("\",\"seq\":"));
  Serial.print(sequenceNumber++);
  Serial.print(F(",\"ts_ms\":"));
  Serial.print(millis());
  Serial.print(F(",\"mode_id\":\""));
  Serial.print(modeIdString());
  Serial.println(F("\",\"error\":\"imu_error\"}"));
}

void clearModeZero(uint8_t mode) {
  if (mode >= INPUT_MODE_COUNT) {
    return;
  }

  modeZeros[mode].ax = 0.0f;
  modeZeros[mode].ay = 0.0f;
  modeZeros[mode].az = 0.0f;
  modeZeros[mode].gx = 0.0f;
  modeZeros[mode].gy = 0.0f;
  modeZeros[mode].gz = 0.0f;
  modeZeros[mode].valid = false;
  if (mode == static_cast<uint8_t>(inputMode)) {
    resetVerticalGesture();
  }
}

void clearAllModeZeros() {
  for (uint8_t mode = 0; mode < INPUT_MODE_COUNT; ++mode) {
    clearModeZero(mode);
  }
}

void captureModeZero(const ImuSample& sample) {
  if (calibrationState != CAL_READY) {
    printComment("ZERO_REJECTED,gyro_calibration_not_ready");
    return;
  }

  ModeZero& zero = modeZeros[static_cast<uint8_t>(inputMode)];
  zero.ax = sample.ax;
  zero.ay = sample.ay;
  zero.az = sample.az;
  zero.gx = sample.gx - gyroBiasX;
  zero.gy = sample.gy - gyroBiasY;
  zero.gz = sample.gz - gyroBiasZ;
  zero.valid = true;
  resetVerticalGesture();

  printComment("ZERO_SET");
}

void resetVerticalGesture() {
  verticalGestureState = VERTICAL_NEUTRAL;
  upDownCount = 0;
}

float verticalRelativeAcceleration(const ImuSample& sample) {
  const ModeZero& zero = modeZeros[static_cast<uint8_t>(inputMode)];
  if (!zero.valid) {
    return 0.0f;
  }

  switch (VERTICAL_ACCEL_AXIS) {
    case 0:
      return sample.ax - zero.ax;
    case 2:
      return sample.az - zero.az;
    case 1:
    default:
      return sample.ay - zero.ay;
  }
}

void updateVerticalGesture(const ImuSample& sample) {
  if (inputMode != MODE_SHORTCUT) {
    resetVerticalGesture();
    return;
  }

  const ModeZero& zero = modeZeros[static_cast<uint8_t>(inputMode)];
  if (!zero.valid) {
    return;
  }

  const float vertical = verticalRelativeAcceleration(sample);
  if (vertical >= VERTICAL_GESTURE_THRESHOLD_G) {
    verticalGestureState = VERTICAL_UP;
    return;
  }

  if (vertical <= -VERTICAL_GESTURE_THRESHOLD_G) {
    // 위로 움직인 뒤 아래로 움직인 한 쌍을 1회로 계산합니다.
    if (verticalGestureState == VERTICAL_UP && upDownCount < 255) {
      ++upDownCount;
      printComment("VERTICAL_COUNT");
      if (upDownCount == EASTER_EGG_CYCLE_THRESHOLD) {
        printComment("EASTER_READY,pc_must_confirm_left_and_right");
      }
    }
    verticalGestureState = VERTICAL_DOWN;
  }
}

// 추후 OLED를 연결하면 이 함수에서 화면 글리치를 표시합니다.
void updateEasterEggDisplay(bool glitchOn) {
  // TODO: OLED 화면 깨짐/노이즈 표현
  (void)glitchOn;
}

void startEasterEgg() {
  if (inputMode != MODE_SHORTCUT) {
    printComment("EASTER_REJECTED,shortcut_mode_only");
    return;
  }

  easterEggActive = true;
  easterEggEndMs = millis() + EASTER_EGG_DURATION_MS;
  easterEggLedStep = 0;
  lastLedToggleMs = 0;
  updateEasterEggDisplay(true);
  printComment("EASTER_EGG,GLITCH_ON");
}

void stopEasterEgg() {
  easterEggActive = false;
  updateEasterEggDisplay(false);
  printComment("EASTER_EGG,GLITCH_OFF");
}

void updateEasterEgg(uint32_t nowMs) {
  if (!easterEggActive) {
    return;
  }

  if (static_cast<int32_t>(nowMs - easterEggEndMs) >= 0) {
    stopEasterEgg();
  }
}

void requestModeZeroCapture() {
  zeroCaptureRequested = true;
  printComment("ZERO_CAPTURE_PENDING,hold_sensor_still");
}

void startCalibration() {
  gyroBiasX = 0.0f;
  gyroBiasY = 0.0f;
  gyroBiasZ = 0.0f;
  gyroSumX = 0.0f;
  gyroSumY = 0.0f;
  gyroSumZ = 0.0f;
  calibrationSamples = 0;
  calibrationStartMs = millis();
  calibrationState = CALIBRATING;
  clearAllModeZeros();
  printComment("CALIBRATION_START,keep_sensor_still");
  emitCalibrationEvent("started");
}

void updateCalibration(const ImuSample& sample) {
  if (calibrationState != CALIBRATING) {
    return;
  }

  gyroSumX += sample.gx;
  gyroSumY += sample.gy;
  gyroSumZ += sample.gz;
  ++calibrationSamples;

  const uint32_t elapsed = millis() - calibrationStartMs;
  if (elapsed < CALIBRATION_TIME_MS || calibrationSamples < 20) {
    return;
  }

  const float count = static_cast<float>(calibrationSamples);
  gyroBiasX = gyroSumX / count;
  gyroBiasY = gyroSumY / count;
  gyroBiasZ = gyroSumZ / count;
  calibrationState = CAL_READY;

  printComment("CALIBRATION_DONE");
  emitCalibrationEvent("complete");
}

void updateLed(uint32_t nowMs) {
  if (easterEggActive) {
    // 짧고 불규칙한 점멸로 글리치 느낌을 냅니다.
    const uint32_t interval = 25 + ((easterEggLedStep * 37) % 140);
    if (nowMs - lastLedToggleMs >= interval) {
      lastLedToggleMs = nowMs;
      const bool glitchOn = ((easterEggLedStep % 5) != 1);
      ++easterEggLedStep;
      setLed(glitchOn);
    }
    return;
  }

  if (calibrationState == CAL_READY) {
    setLed(true);
    return;
  }

  const uint32_t interval = (calibrationState == CAL_ERROR) ? 150 : 300;
  if (nowMs - lastLedToggleMs >= interval) {
    lastLedToggleMs = nowMs;
    setLed(!ledState);
  }
}

// -----------------------------------------------------------------------------
// USB 명령
// -----------------------------------------------------------------------------

void updateModeDisplay();

void processCommand(String command) {
  command.trim();
  command.toUpperCase();

  if (command == "MODE=0" || command == "MODE=RAW") {
    inputMode = MODE_RAW;
    resetVerticalGesture();
    if (easterEggActive) {
      stopEasterEgg();
    }
    printComment("MODE=0");
    updateModeDisplay();
    return;
  }

  if (command == "MODE=1" || command == "MODE=MOTION") {
    inputMode = MODE_MOTION;
    resetVerticalGesture();
    if (easterEggActive) {
      stopEasterEgg();
    }
    printComment("MODE=1");
    updateModeDisplay();
    return;
  }

  if (command == "MODE=2" || command == "MODE=SHORTCUT") {
    inputMode = MODE_SHORTCUT;
    resetVerticalGesture();
    printComment("MODE=2");
    updateModeDisplay();
    return;
  }

  if (command == "ZERO" || command == "ZERO=SET" || command == "ZERO=CAPTURE") {
    requestModeZeroCapture();
    return;
  }

  if (command == "ZERO=CLEAR") {
    clearModeZero(static_cast<uint8_t>(inputMode));
    printComment("ZERO_CLEARED");
    return;
  }

  if (command == "ZERO=CLEAR_ALL") {
    clearAllModeZeros();
    printComment("ZERO_ALL_CLEARED");
    return;
  }

  if (command == "EASTER=ON" || command == "GLITCH") {
    startEasterEgg();
    return;
  }

  if (command == "EASTER=OFF") {
    stopEasterEgg();
    return;
  }

  if (command == "CAL" || command == "CALIBRATE" || command == "CAL_GYRO") {
    startCalibration();
    return;
  }

  if (command == "PING") {
    printComment("PONG");
    return;
  }

  if (command == "INFO") {
    printComment("INFO");
    return;
  }

  if (command.length() > 0) {
    printComment("UNKNOWN_COMMAND");
  }
}

void printModeChanged() {
  printComment("MODE_CHANGED");
}

// 추후 OLED를 연결하면 이 함수에서 현재 모드를 표시합니다.
// 현재는 OLED 라이브러리와 배선을 사용하지 않습니다.
void updateModeDisplay() {
  // TODO: OLED에 RAW / MOTION / SHORTCUT 표시
}

void changeMode(int8_t delta) {
  int8_t next = static_cast<int8_t>(inputMode) + delta;
  if (next < 0) {
    next = 2;
  } else if (next > 2) {
    next = 0;
  }
  inputMode = static_cast<InputMode>(next);
  resetVerticalGesture();
  if (easterEggActive && inputMode != MODE_SHORTCUT) {
    stopEasterEgg();
  }
  printModeChanged();
  updateModeDisplay();
}

void updateButton(ButtonState& button, uint8_t pin, int8_t modeDelta, uint32_t nowMs) {
  const bool pressed = (digitalRead(pin) == LOW);

  if (pressed != button.lastReading) {
    button.lastReading = pressed;
    button.lastChangeMs = nowMs;
  }

  // 35 ms debounce 후 버튼 상태를 확정합니다.
  if ((nowMs - button.lastChangeMs) >= 35 && pressed != button.stablePressed) {
    button.stablePressed = pressed;
    if (button.stablePressed) {
      button.pressStartMs = nowMs;
      button.longPressHandled = false;
    } else if (!button.longPressHandled) {
      // 짧게 누른 경우에만 모드를 변경합니다.
      changeMode(modeDelta);
    }
  }

  if (button.stablePressed && !button.longPressHandled &&
      (nowMs - button.pressStartMs) >= BUTTON_LONG_PRESS_MS) {
    button.longPressHandled = true;

    if (modeDelta > 0) {
      // BUTTON1 1.5초 이상: 현재 모드의 자세를 6축 영점으로 저장
      requestModeZeroCapture();
    } else {
      // BUTTON2 1.5초 이상: 현재 모드의 영점 삭제
      clearModeZero(static_cast<uint8_t>(inputMode));
      printComment("ZERO_CLEARED");
    }
  }
}

void updateModeButtons(uint32_t nowMs) {
  updateButton(nextModeButton, LEFT_BUTTON_PIN, +1, nowMs);
  updateButton(previousModeButton, RIGHT_BUTTON_PIN, -1, nowMs);
}

void readUsbCommands() {
  while (Serial.available() > 0) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\n' || c == '\r') {
      if (commandBuffer.length() > 0) {
        processCommand(commandBuffer);
        commandBuffer = "";
      }
      continue;
    }

    if (commandBuffer.length() < 40) {
      commandBuffer += c;
    }
  }
}

// -----------------------------------------------------------------------------
// 초기화 및 메인 루프
// -----------------------------------------------------------------------------

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  setLed(false);

  pinMode(LEFT_BUTTON_PIN, INPUT_PULLUP);
  pinMode(RIGHT_BUTTON_PIN, INPUT_PULLUP);

  Serial.begin(115200);
  commandBuffer.reserve(40);
  delay(50);

  // 손 구분은 버튼이 아니라 보드별 컴파일 설정으로 고정합니다.
  handSide = DEFAULT_HAND_SIDE;
  Wire.begin();  // NU40 DK 기본 I2C: SDA=P0.26, SCL=P0.27
  Wire.setClock(400000);

  printComment("NU40_ANDREW_FW_JSONL");
  printComment("READY");
  updateModeDisplay();

  if (!imu.begin(Wire)) {
    calibrationState = CAL_ERROR;
    printComment("ERROR,LSM6DS3TR_not_found,check_Qwiic_and_sensor_model");
    return;
  }

  printComment("IMU_LSM6DS3TR_READY");

  startCalibration();
  nextSampleUs = micros();
}

void loop() {
  const uint32_t nowMs = millis();
  readUsbCommands();
  updateModeButtons(nowMs);
  updateEasterEgg(nowMs);
  updateLed(nowMs);

  if (calibrationState == CAL_ERROR) {
    if (nowMs - lastErrorReportMs >= 1000) {
      lastErrorReportMs = nowMs;
      emitSensorError();
    }
    return;
  }

  const uint32_t nowUs = micros();
  if (static_cast<int32_t>(nowUs - nextSampleUs) < 0) {
    return;
  }
  nextSampleUs = nowUs + SAMPLE_PERIOD_US;

  ImuSample sample;
  if (!imu.read(sample)) {
    calibrationState = CAL_ERROR;
    printComment("ERROR,imu_read_failed");
    return;
  }

  updateCalibration(sample);

  if (zeroCaptureRequested) {
    captureModeZero(sample);
    zeroCaptureRequested = false;
  }

  updateVerticalGesture(sample);
  emitSample(sample);
}
