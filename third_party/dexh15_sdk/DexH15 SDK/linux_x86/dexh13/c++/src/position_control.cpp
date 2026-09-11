#include <dexh13/dexh13_control.h>

#include <cmath>
#include <sstream>
#include <thread>

/**
 * @brief DexH13 SDK 位置模式
 * @details
 *          步骤：
 *          (1) 连接灵巧手
 *          (2) 初始化电机位置
 *          (3) 设置控制模式-位置模式
 *          (4) 使能电机
 *          (5) 设置关节弧度
 *          (6) 断开灵巧手连接
 * @return
 */
int main(int argc, char **argv) {
  std::shared_ptr<paxini::bot::dexh13::DexH13Control> dexh13 = std::make_shared<paxini::bot::dexh13::DexH13Control>();
  // 连接灵巧手
  int handy_type = dexh13->activeHandy("/dev/ttyUSB0", "none");
  std::cout << "连接灵巧手成功, 灵巧手类型为" << (handy_type == 1 ? "左手" : "右手") << std::endl;
  std::this_thread::sleep_for(std::chrono::seconds(1));
  // 初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
  int is_success = dexh13->initMotorPosition();
  std::cout << "初始化电机位置, 结果为" << (is_success ? "成功" : "失败") << std::endl;
  std::this_thread::sleep_for(std::chrono::seconds(1));
  // 设置控制模式-位置模式
  bool is_true = dexh13->setMotorControlMode(paxini::bot::dexh13::ControlMode::POSITION_CONTROL_MODE);
  std::cout << "设置控制模式(位置模式), 结果为" << (is_true ? "成功" : "失败") << std::endl;
  std::this_thread::sleep_for(std::chrono::seconds(1));
  // 使能电机
  is_true = dexh13->enableMotor();
  std::cout << "使能电机, 结果为" << (is_true ? "成功" : "失败") << std::endl;
  std::this_thread::sleep_for(std::chrono::seconds(1));
  // 设置关节弧度
  std::vector<double> radians = {
      M_PI / 180 * 10.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0,   // 食指 indexFinger
      M_PI / 180 * 10.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0,   // 中指 middleFinger
      M_PI / 180 * 10.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0,   // 环指 ringFinger
      M_PI / 180 * 10.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0};  // 大拇指 thumb
  is_success = dexh13->setJointPositionsRadian(radians);
  std::cout << "设置关节弧度, 结果为" << (is_success ? "成功" : "失败") << std::endl;
  std::this_thread::sleep_for(std::chrono::seconds(1));
  // 断开灵巧手连接
  is_success = dexh13->disconnectHandy();
  std::cout << (is_success ? "断开灵巧手连接, 结果为成功" : "断开灵巧手连接, 结果为失败") << std::endl;
  return 0;
}