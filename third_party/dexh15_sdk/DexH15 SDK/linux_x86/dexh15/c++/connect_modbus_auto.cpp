#include <dexh15/dexh15_control.h>

#include <iostream>

/**
 *  @brief Modbus 自动连接
 *  @details 步骤：
 *               (1) 自动连接 Modbus 串口下的第一个从站
 *               (2) 查询灵巧手在线状态
 *               (3) 关闭 Modbus 串口
 */
int main(int argc, char **argv) {
  std::shared_ptr<paxini::bot::dexh15::DexH15Control> dexh15 = std::make_shared<paxini::bot::dexh15::DexH15Control>();
  // Modbus 串口号
  std::string hand_port_num = "/dev/ttyUSB0";
  // Modbus 从站地址
  int16_t device_address;
  // 自动连接 Modbus 串口下的第一个从站
  if (!dexh15->connectModbusAuto(hand_port_num, device_address)) {
    std::cerr << "请检查灵巧手是否接入 Modbus 串口设备" << std::endl;
    return 1;
  }
  std::cout << "自动连接灵巧手成功" << std::endl;
  // 查询灵巧手在线状态
  bool is_connected = dexh15->isModbusDeviceConnected(device_address);
  std::cout << "灵巧手" << (is_connected ? "在线" : "离线") << std::endl;
  // 关闭 Modbus 串口
  bool result = dexh15->disconnectModbus();
  std::cout << "断开 Modbus 串口设备 " << hand_port_num << (is_connected ? " 成功" : " 失败") << std::endl;
  return 0;
}