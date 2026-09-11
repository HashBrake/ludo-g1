#include <dexh13/dexh13_control.h>

#include <sstream>
#include <thread>

int main(int argc, char **argv) {
  std::shared_ptr<paxini::bot::dexh13::DexH13Control> dexh13 = std::make_shared<paxini::bot::dexh13::DexH13Control>();
  // 连接灵巧手
  int handy_type = dexh13->activeHandy("/dev/ttyUSB0", "none");
  std::cout << "连接灵巧手成功, 灵巧手类型为" << (handy_type == 1 ? "左手" : "右手") << std::endl;
  std::this_thread::sleep_for(std::chrono::seconds(1));
  // 获取传感器数据(按住指尖传感器,数值才有变化)
  int hz = 10;
  while (true) {
    std::vector<paxini::bot::dexh13::ForcePoint> force_points = dexh13->getFingerTactile();
    for (auto force_point : force_points) {
      std::cout << "x:" << force_point.x << ", y:" << force_point.y << ", z:" << force_point.z << std::endl;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(static_cast<int>(1000.0 / hz)));
  }
  return 0;
}