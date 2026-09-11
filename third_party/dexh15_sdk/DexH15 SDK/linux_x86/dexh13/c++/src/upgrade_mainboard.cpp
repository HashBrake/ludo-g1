#include <dexh13/dexh13_control.h>

#include <cmath>
#include <sstream>
#include <thread>

/**
 * @brief 打印升级进度条
 */
int processFunc(std::shared_ptr<paxini::bot::dexh13::DexH13Control> dexh13) {
  int i = 0;
  while (i < 30) {
    bool is_finished = false;
    const std::vector<paxini::bot::dexh13::UpgradeProcess> process_info = dexh13->getUpgradeInfo();
    for (const auto &process : process_info) {
      std::cout << "升级类型：" << process.upgrade_type << ", "
                << "固件：" << process.part << ", "
                << "进度：" << process.process_data << ", "
                << "状态：" << process.status << std::endl;
      is_finished = (process.process_data == 100);
    }
    if (is_finished) {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1000));
    i++;
  }
  return 1;
}

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
  // 升级主控固件
  std::thread t1(processFunc, dexh13);
  // 主控版本固件文件需与可执行文件保持路径一致
  std::vector<std::string> file_names = {"AX58400_DexH13_CM7_V2.0.1.efw", "AX58400_DexH13_CM4_V2.0.1.efw"};
  paxini::bot::dexh13::UpgradeErrorCode result_code = dexh13->upgradeMainboard(file_names);
  std::cout << "升级主控固件结果为" << (static_cast<int>(result_code) == 1003 ? "成功" : "失败") << std::endl;
  if (t1.joinable()) {
    t1.join();
  }
  // 断开灵巧手连接, 升级完主控版本后，建议对灵巧手重新上电
  is_success = dexh13->disconnectHandy();
  std::cout << "断开灵巧手" << (is_success ? "成功" : "失败") << std::endl;
  return 0;
}