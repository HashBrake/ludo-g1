#include <dexh15/dexh15_control.h>

#include <iostream>

/**
 *  @brief Modbus 手动链接
 *  @details 步骤：
 *               (1) 打开 Modbus 串口
 *               (2) 初始化 Modbus 从站
 *               (3) 初始化电机位置(如果刚上电时已调用，可跳过)
 *               (4) 查询灵巧手在线状态
 *               (5) 关闭 Modbus 串口
 */
int main(int argc, char **argv) {
  std::shared_ptr<paxini::bot::dexh15::DexH15Control> dexh15 = std::make_shared<paxini::bot::dexh15::DexH15Control>();
  // Modbus 串口号
  std::string hand_port_num = "/dev/ttyUSB0";
  // 波特率
  int32_t baud_rate = 4000000;
  // Modbus 从站地址
  int16_t device_address = 0x79;
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
  std::cout << "灵巧手" << (is_connected ? "在线" : "离线") << std::endl;
  // 关闭 Modbus 串口
  bool result = dexh15->disconnectModbus();
  std::cout << "断开 Modbus 串口设备 " << hand_port_num << (is_connected ? " 成功" : " 失败") << std::endl;
  return 0;
}