#include <dexh15/dexh15_control.h>

#include <chrono>
#include <iostream>
#include <thread>

/**
 *  @brief 法向力力位混合控制模式
 *  @details 步骤：
 *               (1) 打开 Modbus 串口
 *               (2) 初始化 Modbus 从站
 *               (3) 初始化电机位置(如果刚上电时已调用，可跳过)
 *               (4) 查询灵巧手在线状态
 *               (5) 设置位置模式
 *               (6) 使能电机
 *               (7) 查询使能状态
 *               (8) 设置法向力力控参数
 *               (9) 计算下次运动的目标角度
 *               (10) 设置下次运动目标角度
 *               (11) 重复步骤(9)和(10)，直至握住物体且法向力恒定
 *               (12) 下使能电机
 *               (13) 关闭 Modbus 串口
 */
int main(int argc, char **argv) {
  std::shared_ptr<paxini::bot::dexh15::DexH15Control> dexh15 = std::make_shared<paxini::bot::dexh15::DexH15Control>();
  // Modbus 串口号
  std::string hand_port_num = "/dev/ttyUSB0";
  // 波特率
  int32_t baud_rate = 4000000;
  // Modbus 从站地址
  int16_t device_address = 0x78;
  // 打开 Modbus 串口
  if (!dexh15->openModbusDevice(hand_port_num, baud_rate)) {
    std::cerr << "请检查是否插入 Modbus 串口设备" << std::endl;
    return 1;
  }
  std::cout << "打开 Modbus 串口成功" << std::endl;
  // 初始化 Modbus 从站
  if (!dexh15->initModbusDevice(device_address)) {
    std::cerr << "请检查灵巧手是否接入 Modbus 串口设备" << std::endl;
    dexh15->disconnectModbus();
    return 1;
  }
  std::cout << "初始化 Modbus 从站成功" << std::endl;
  // 初始化电机位置(如果刚上电时已调用，可跳过)
  if (!dexh15->initMotorPosition(device_address)) {
    std::cerr << "初始化电机位置失败" << std::endl;
    dexh15->disconnectModbus();
    return 1;
  }
  std::cout << "初始化电机位置成功" << std::endl;
  // 查询灵巧手在线状态
  bool is_connected = dexh15->isModbusDeviceConnected(device_address);
  std::cout << "灵巧手连接状态为" << (is_connected ? "在线" : "离线") << std::endl;
  // 设置位置模式
  if (!dexh15->setMotorControlMode(device_address, paxini::bot::dexh15::POSITION_CONTROL_MODE)) {
    std::cerr << "设置位置模式失败，无法进行位置模式控制" << std::endl;
    dexh15->disconnectModbus();
    return 1;
  }
  std::cout << "设置位置模式成功" << std::endl;
  // 使能电机
  if (!dexh15->enableMotor(device_address)) {
    std::cerr << "使能电机失败，无法进行位置模式控制" << std::endl;
    dexh15->disconnectModbus();
    return 1;
  }
  std::cout << "使能电机成功" << std::endl;
  // 查询使能状态
  if (!dexh15->isMotorEnabled(device_address)) {
    std::cerr << "电机状态为未使能，无法进行位置模式控制" << std::endl;
    dexh15->disconnectModbus();
    return 1;
  }
  std::cout << "电机状态为已使能" << std::endl;
  // 法向力力控参数
  paxini::bot::dexh15::ForceCtlParam force_ctl_param{{50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50},
                                                     {5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5},
                                                     {true, true, true, true, false, false}};
  // 初始位姿
  std::vector<double> original_angle = {0, 0, 0, 0, 0, 0, 0, 0, 300, 0, 0};
  // 设置法向力力控参数
  if (!dexh15->setCommonForceCtlParam(device_address, original_angle, force_ctl_param)) {
    std::cerr << "设置法向力力控参数失败" << std::endl;
    dexh15->disconnectModbus();
    return 1;
  }
  // 休眠等待到达初始位姿
  std::this_thread::sleep_for(std::chrono::milliseconds(1500));
  std::cout << "设置法向力力控参数成功" << std::endl;
  // 执行法向力力位混合模式控制
  auto start_time = std::chrono::steady_clock::now();
  auto duration = std::chrono::seconds(30);
  while (std::chrono::steady_clock::now() - start_time < duration) {
    std::vector<double> command;
    if (!dexh15->computeCommonForceTargetCommand(device_address, command)) {
      std::cerr << "计算下次运动的目标角度失败" << std::endl;
      break;
    }
    std::cout << "计算下次运动的目标角度成功" << std::endl;
    if (!dexh15->setJointPositionsAngle(device_address, command)) {
      std::cerr << "设置下次运动目标角度失败" << std::endl;
      break;
    }
    std::cout << "设置下次运动目标角度成功" << std::endl;
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  }
  // 电机回到零位
  paxini::bot::dexh15::Dex15MotorPosition zero_positions{0, 0, 0, 0, 0, 0, 0};
  if (!dexh15->setMotorTargetPosition(device_address, zero_positions)) {
    std::cerr << "电机位置回零失败" << std::endl;
  }
  std::cout << "电机位置回零成功" << std::endl;
  bool is_success = dexh15->disableMotor(device_address);
  std::cout << "电机下使能" << (is_success ? "成功" : "失败") << std::endl;
  // 关闭 Modbus 串口
  bool result = dexh15->disconnectModbus();
  std::cout << "断开 Modbus 串口设备 " << hand_port_num << (is_connected ? " 成功" : " 失败") << std::endl;
  return 0;
}