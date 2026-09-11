#include <dexh15/dexh15_control.h>

#include <iostream>

/**
 *  @brief USB 直连
 *  @details 步骤：
 *               (1) 打开 USB 串口
 *               (2) 初始化电机位置(如果刚上电时已调用，可跳过)
 *               (3) 查询灵巧手在线状态
 *               (4) 关闭 USB 串口
 */
int main(int argc, char **argv) {
  std::shared_ptr<paxini::bot::dexh15::DexH15Control> dexh15 = std::make_shared<paxini::bot::dexh15::DexH15Control>();
  // USB 串口号
  std::string hand_port_num = "/dev/ttyACM0";
  // 从站地址
  int16_t device_address = 0x79;
  // 打开 USB 串口
  if (!dexh15->openUsbDevice(hand_port_num, device_address)) {
    std::cerr << "请检查是否插入 USB 串口设备" << std::endl;
    return 1;
  }
  std::cout << "打开 USB 串口成功" << std::endl;
  // 初始化电机位置(如果刚上电时已调用，可跳过)
  if (!dexh15->initMotorPosition(device_address)) {
    std::cerr << "初始化电机位置失败" << std::endl;
    dexh15->disconnectUsb();
    return 1;
  }
  std::cout << "初始化电机位置成功" << std::endl;
  // 查询灵巧手在线状态
  bool is_connected = dexh15->isModbusDeviceConnected(device_address);
  std::cout << "灵巧手" << (is_connected ? "在线" : "离线") << std::endl;
  // 关闭 USB 串口
  bool result = dexh15->disconnectUsb();
  std::cout << "断开 USB 串口设备 " << hand_port_num << (is_connected ? " 成功" : " 失败") << std::endl;
  return 0;
}