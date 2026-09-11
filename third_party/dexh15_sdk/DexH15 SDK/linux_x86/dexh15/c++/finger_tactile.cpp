#include <dexh15/dexh15_control.h>

#include <sstream>
#include <thread>

/**
 *  @brief 读取传感器数据
 *  @details 步骤：
 *               (1) 打开 Modbus 串口
 *               (2) 初始化 Modbus 从站
 *               (3) 校准传感器
 *               (4) 读取传感器合力
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
  std::this_thread::sleep_for(std::chrono::seconds(1));
  // 校准传感器
  if (!dexh15->calibrateSensor(device_address)) {
    std::cerr << "校准传感器失败" << std::endl;
  }
  std::cout << "校准传感器成功" << std::endl;
  // 获取传感器数据(按住指尖传感器,数值才有变化)
  int hz = 10;
  while (true) {
    std::vector<paxini::bot::dexh15::ForcePoint> force_points = dexh15->getFingerTactile(device_address);
    for (auto force_point : force_points) {
      std::cout << "x:" << force_point.x << ", y:" << force_point.y << ", z:" << force_point.z << std::endl;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(static_cast<int>(1000.0 / hz)));
  }
  return 0;
}