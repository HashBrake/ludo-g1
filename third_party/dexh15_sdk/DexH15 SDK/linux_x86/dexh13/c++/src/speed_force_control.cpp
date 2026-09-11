#include <dexh13/dexh13_control.h>
#include <dexh13/dexh13_force_control.h>
#include <dexh13/dexh13_kinematic.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <sstream>
#include <thread>

void printJointRadians(const std::vector<double> &joint_radians) {
  std::ostringstream oss;
  oss << "[";
  for (const auto &joint_radian : joint_radians) {
    oss << joint_radian << ",";
  }
  std::string result = oss.str();
  result = oss.str();
  result = result.substr(0, result.size() - 1);
  result += "]";
  std::cout << result << std::endl;
}

int main(int argc, char **argv) {
  std::shared_ptr<paxini::bot::dexh13::DexH13Control> dexh13 = std::make_shared<paxini::bot::dexh13::DexH13Control>();
  std::shared_ptr<paxini::bot::dexh13::DexH13ForceControl> dexh13_force_control =
      std::make_shared<paxini::bot::dexh13::DexH13ForceControl>();
  std::shared_ptr<paxini::bot::dexh13::DexH13Kinematic> dexh13_kinematic =
      std::make_shared<paxini::bot::dexh13::DexH13Kinematic>();

  // 连接灵巧手
  int handy_type = dexh13->activeHandy("/dev/ttyUSB0", "none");
  std::cout << "连接灵巧手成功, 灵巧手类型为" << (handy_type == 1 ? "左手" : "右手") << std::endl;
  std::this_thread::sleep_for(std::chrono::seconds(1));
  // 初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
  int is_success = dexh13->initMotorPosition();
  std::cout << "初始化电机位置, 结果为" << (is_success ? "成功" : "失败") << std::endl;
  std::this_thread::sleep_for(std::chrono::seconds(1));
  // 设置控制模式-速度模式
  bool is_true = dexh13->setMotorControlMode(paxini::bot::dexh13::ControlMode::SPEED_CONTROL_MODE);
  std::cout << "设置控制模式(速度模式), 结果为" << (is_true ? "成功" : "失败") << std::endl;
  std::this_thread::sleep_for(std::chrono::seconds(1));
  // 使能电机
  is_true = dexh13->enableMotor();
  std::cout << "使能电机, 结果为" << (is_true ? "成功" : "失败") << std::endl;
  std::this_thread::sleep_for(std::chrono::seconds(1));
  // 设置电机目标速度
  paxini::bot::dexh13::Dex13MotorSpeed speed1{0, 0, 0, 0, 0, 0, 0, 0, 0, 6000, 6000, 0, 0};
  is_success = dexh13->setMotorTargetSpeed(speed1);
  std::vector<double> cur_serial_pos_vec;
  while (true) {
    dexh13->getJointPositionsRadian(cur_serial_pos_vec);
    if (std::abs(1.57 - cur_serial_pos_vec[13]) < 0.1) {
      paxini::bot::dexh13::Dex13MotorSpeed speed3{0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0};
      is_success = dexh13->setMotorTargetSpeed(speed3);
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  }
  paxini::bot::dexh13::Dex13MotorSpeed speed{300, 300, 300, 300, 300, 300, 300, 300, 300, 0, 0, 300, 300};
  is_success = dexh13->setMotorTargetSpeed(speed);
  std::cout << "设置目标速度, 结果为" << (is_success ? "成功" : "失败") << std::endl;
  std::this_thread::sleep_for(std::chrono::seconds(1));
  // 获取当前弧度
  dexh13->getJointPositionsRadian(cur_serial_pos_vec);
  printJointRadians(cur_serial_pos_vec);
  // 逆运动学解析
  auto inverseKinematicParse = [](std::shared_ptr<paxini::bot::dexh13::DexH13Kinematic> dexh13_kinematic,
                                  paxini::bot::dexh13::HandType handy_type, std::vector<double> &cur_serial_pos_vec) {
    std::vector<int16_t> positions;
    dexh13_kinematic->inverseKinematicParse(handy_type, cur_serial_pos_vec, positions);
    return positions;
  };
  // 设置力控参数
  dexh13_force_control->setForceCtlParam(
      paxini::bot::dexh13::HandType(handy_type),
      {{}, {10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0}, {true, true, true, true, true, true, true, true}});
  // 标定传感器
  dexh13->calibrateSensor();
  // 开始进行力控操作
  auto start_time = std::chrono::steady_clock::now();
  auto duration = std::chrono::seconds(4);
  while (std::chrono::steady_clock::now() - start_time < duration) {
    // 当前传感器合力数据
    std::vector<paxini::bot::dexh13::ForcePoint> force_points = dexh13->getFingerTactile();
    // 当前电机位置
    std::vector<int16_t> current_positions =
        inverseKinematicParse(dexh13_kinematic, paxini::bot::dexh13::HandType(handy_type), cur_serial_pos_vec);
    // 计算下次运动的目标速度
    std::vector<double> command;
    dexh13_force_control->computeSerialTargetCommand(paxini::bot::dexh13::HandType(handy_type), cur_serial_pos_vec,
                                                     force_points, command);
    printJointRadians(command);
    std::vector<int16_t> new_positions =
        inverseKinematicParse(dexh13_kinematic, paxini::bot::dexh13::HandType(handy_type), command);
    std::vector<int16_t> next_speed_value;
    std::transform(new_positions.begin(), new_positions.end(), current_positions.begin(),
                   std::back_inserter(next_speed_value),
                   [](int16_t a, int16_t b) -> int16_t { return std::round(std::abs(static_cast<double>(a - b))); });

    paxini::bot::dexh13::Dex13MotorSpeed next_speed;
    std::memcpy(&next_speed, next_speed_value.data(), 13 * sizeof(int16_t));
    // 设置下次运动的目标速度
    dexh13->setMotorTargetSpeed(next_speed);
    // 获取当前弧度
    dexh13->getJointPositionsRadian(cur_serial_pos_vec);
  }

  // 断开灵巧手连接
  is_success = dexh13->disconnectHandy();
  std::cout << (is_success ? "断开灵巧手连接, 结果为成功" : "断开灵巧手连接, 结果为失败") << std::endl;
  return 0;
}