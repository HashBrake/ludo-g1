#include "dexh5_example.h"

#include <math.h>

#include <sstream>
#include <thread>

DexH5Example::DexH5Example(const std::string& hand_port_num) { this->hand_port_num = hand_port_num; }

DexH5Example::~DexH5Example() {}

void DexH5Example::getVersion() {
  std::shared_ptr<paxini::bot::dexh5::DexH5Control> dexh5 = std::make_shared<paxini::bot::dexh5::DexH5Control>();
  // 步骤：连接灵巧手
  try {
    int handy_type = dexh5->connectDevice(this->hand_port_num);
    std::cout << "步骤: 连接灵巧手成功，灵巧手类型为" << (handy_type == 1 ? "左手" : "右手") << std::endl;
  } catch (...) {
    throw std::runtime_error("连接灵巧手失败，请检查灵巧手串口是否正确");
    return;
  }
  // 步骤：查询灵巧手连接状态
  bool is_online = dexh5->isConnected();
  if (!is_online) {
    throw std::runtime_error("灵巧手未连接");
    return;
  }
  std::cout << "步骤: 灵巧手已连接" << std::endl;
  // 步骤：查询版本信息
  auto getVersion = dexh5->getVersion();
  std::cout << "固件版本为 " << getVersion.firmware_version << std::endl;
  std::cout << "SDK版本为 " << getVersion.sdk_version << std::endl;
  // 步骤：断开灵巧手连接
  int is_success = dexh5->disconnectDevice();
  std::cout << (is_success ? "断开灵巧手连接成功" : "断开灵巧手连接失败") << std::endl;
}

void DexH5Example::runGestures() {
  std::shared_ptr<paxini::bot::dexh5::DexH5Control> dexh5 = std::make_shared<paxini::bot::dexh5::DexH5Control>();
  // 步骤：连接灵巧手
  try {
    int handy_type = dexh5->connectDevice(this->hand_port_num);
    std::cout << "步骤: 连接灵巧手成功，灵巧手类型为" << (handy_type == 1 ? "左手" : "右手") << std::endl;
  } catch (...) {
    throw std::runtime_error("连接灵巧手失败，请检查灵巧手串口是否正确");
    return;
  }
  // 步骤：查询灵巧手连接状态
  bool is_online = dexh5->isConnected();
  if (!is_online) {
    throw std::runtime_error("灵巧手未连接");
    return;
  }
  std::cout << "步骤: 灵巧手已连接" << std::endl;

  std::vector<std::vector<double>> targets = {{0, 0, 0, 0, 0}, {0, 0, 75, 0, 40.5}, {47, 75, 75, 47, 29}};
  while (true) {
    for (auto target : targets) {
      paxini::bot::dexh5::HReturnCode return_code = dexh5->setJointPositionsDegree(target);
      std::cout << "执行手势" << (return_code == paxini::bot::dexh5::HReturnCode::SUCCESS ? "成功" : "失败")
                << std::endl;

      std::this_thread::sleep_for(std::chrono::seconds(2));

      std::vector<double> current_joint_degree;
      return_code = dexh5->getJointPositionsDegree(current_joint_degree);
      if (return_code == paxini::bot::dexh5::HReturnCode::SUCCESS) {
        this->printJointDegree(current_joint_degree);
      }
    }
  }
}

void DexH5Example::getFingerTactile() {
  std::shared_ptr<paxini::bot::dexh5::DexH5Control> dexh5 = std::make_shared<paxini::bot::dexh5::DexH5Control>();
  // 步骤：连接灵巧手
  try {
    int handy_type = dexh5->connectDevice(this->hand_port_num);
    std::cout << "步骤: 连接灵巧手成功，灵巧手类型为" << (handy_type == 1 ? "左手" : "右手") << std::endl;
  } catch (...) {
    throw std::runtime_error("连接灵巧手失败，请检查灵巧手串口是否正确");
    return;
  }
  // 步骤：查询灵巧手连接状态
  bool is_online = dexh5->isConnected();
  if (!is_online) {
    throw std::runtime_error("灵巧手未连接");
    return;
  }
  std::cout << "步骤: 灵巧手已连接" << std::endl;
  paxini::bot::dexh5::HReturnCode return_code;
  // 步骤：标定灵巧手触觉传感器（校准操作）
  return_code = dexh5->calibrateTactile();
  std::cout << "步骤: 标定灵巧手触觉传感器，结果为"
            << (return_code == paxini::bot::dexh5::HReturnCode::SUCCESS == 1 ? "成功" : "失败") << std::endl;
  // 步骤：查询触觉传感器
  int hz = 10;
  while (true) {
    std::vector<paxini::bot::dexh5::H5ForcePoint> force_points;
    return_code = dexh5->getTactileResultantForce(force_points);
    if (return_code == paxini::bot::dexh5::HReturnCode::SUCCESS) {
      this->printForcePoints(force_points);
    } else {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(static_cast<int>(1000.0 / hz)));
  }
}

void DexH5Example::inverseKinematicParse() {
  std::shared_ptr<paxini::bot::dexh5::DexH5Kinematic> dexh5_kinematic =
      std::make_shared<paxini::bot::dexh5::DexH5Kinematic>();
  // 需要解析的弧度集合
  std::vector<double> radians = {M_PI / 180 * 50.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0, M_PI / 180 * 20.0,
                                 M_PI / 180 * 20.0};

  // 解析获取对应的实际电机位置集合
  auto hand_type = paxini::bot::dexh5::HandType::LEFT_HAND;  // 请根据左右手类型选择，这里以左手为例
  std::vector<int16_t> target_positions;
  dexh5_kinematic->inverseKinematicParse(hand_type, radians, target_positions);
  for (int i = 0; i < target_positions.size(); i++) {
    std::cout << "motor " << i + 1 << " : " << target_positions[i] << std::endl;
  }
}

void DexH5Example::forwardKinematicParse() {
  std::shared_ptr<paxini::bot::dexh5::DexH5Kinematic> dexh5_kinematic =
      std::make_shared<paxini::bot::dexh5::DexH5Kinematic>();
  // 电机实际位置值集合
  std::vector<int16_t> motor_positions = {836, 834, 835, 0, 0};

  // 解析获取对应的实际手指弧度集合
  auto hand_type = paxini::bot::dexh5::HandType::LEFT_HAND;  // 请根据左右手类型选择，这里以左手为例
  std::vector<double> target_radians;
  dexh5_kinematic->forwardKinematicParse(hand_type, motor_positions, target_radians);

  for (const auto& radian : target_radians) {
    std::cout << radian << std::endl;
  }
}

void DexH5Example::printJointDegree(const std::vector<double>& degrees) {
  std::ostringstream oss;
  oss << "角度为 [";
  for (int i = 0; i < degrees.size(); i++) {
    oss << degrees[i];
    if (i != degrees.size() - 1) {
      oss << ",";
    }
  }
  oss << "]";
  std::cout << oss.str() << std::endl;
}

void DexH5Example::printForcePoints(std::vector<paxini::bot::dexh5::H5ForcePoint>& force_points) {
  std::ostringstream oss;
  for (auto info : force_points) {
    oss << "Finger_name: " << info.finger_name << ", "
        << "Sensor_id: " << info.sensor_id << ", "
        << "Resultant Force (X, Y, Z): (" << info.x << ", " << info.y << ", " << info.z << ")\n";
  }
  std::cout << oss.str() << std::endl;
}