#include <dexh13/dexh13_control.h>
#include <dexh13/dexh13_force_control.h>

#include <cmath>
#include <thread>

/**
 *  @brief 力控模式
    步骤：
        (1) 连接灵巧手
        (2) 初始化电机位置
        (3) 设置控制模式-位置模式
        (4) 使能电机
        (5) 设置力控参数
        (6) 获取当前弧度
        (7) 获取当前传感器合力数据
        (8) 计算下次运动的目标弧度
        (9) 重复步骤(6)、步骤(7)和步骤(8)，直至握住物体
        (10) 断开灵巧手连接
 */
int main(int argc, char **argv) {
  std::shared_ptr<paxini::bot::dexh13::DexH13Control> dexh13 = std::make_shared<paxini::bot::dexh13::DexH13Control>();
  std::shared_ptr<paxini::bot::dexh13::DexH13ForceControl> dexh13_force_control =
      std::make_shared<paxini::bot::dexh13::DexH13ForceControl>();

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
  // 设置起始位置
  dexh13->setJointPositionsRadian({0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, M_PI / 180 * 90, 0, 0});
  std::this_thread::sleep_for(std::chrono::seconds(1));
  // 设置力控参数
  dexh13_force_control->setForceCtlParam(
      paxini::bot::dexh13::HandType(handy_type),
      {{}, {10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0}, {true, true, true, true, true, true, true, true}});
  // 获取当前弧度
  std::vector<double> cur_serial_pos_vec;
  dexh13->getJointPositionsRadian(cur_serial_pos_vec);
  // 开始进行力控操作
  auto start_time = std::chrono::steady_clock::now();
  auto duration = std::chrono::seconds(5);
  while (std::chrono::steady_clock::now() - start_time < duration) {
    // 当前传感器合力数据
    std::vector<paxini::bot::dexh13::ForcePoint> force_points = dexh13->getFingerTactile();
    // 计算下次运动的目标弧度
    std::vector<double> command;
    dexh13_force_control->computeSerialTargetCommand(paxini::bot::dexh13::HandType(handy_type), cur_serial_pos_vec,
                                                     force_points, command);
    // 设置下次运动的目标弧度
    dexh13->setJointPositionsRadian(command);
    // 获取当前弧度
    dexh13->getJointPositionsRadian(cur_serial_pos_vec);
  }

  // 断开灵巧手连接
  is_success = dexh13->disconnectHandy();
  std::cout << (is_success ? "断开灵巧手连接, 结果为成功" : "断开灵巧手连接, 结果为失败") << std::endl;
  return 0;
}