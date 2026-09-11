#include "dexh13_example.h"

#include <cmath>
#include <opencv2/opencv.hpp>
#include <sstream>
#include <thread>

DexH13Example::DexH13Example(const std::string& hand_port_num, const std::string& camera_port_num) {
  this->hand_port_num = hand_port_num;
  this->camera_port_num = camera_port_num;
}

DexH13Example::~DexH13Example() {}

void DexH13Example::getVersion() {
  std::shared_ptr<paxini::bot::dexh13::DexH13Control> dexh13 = std::make_shared<paxini::bot::dexh13::DexH13Control>();
  // 步骤：激活灵巧手
  int handy_type = dexh13->activeHandy(this->hand_port_num, this->camera_port_num);
  std::cout << "步骤: 激活灵巧手成功, 灵巧手类型为" << (handy_type == 1 ? "左手" : "右手") << std::endl;
  // 步骤：查询灵巧手连接状态
  bool is_online = dexh13->isConnectHandy();
  if (!is_online) {
    throw std::runtime_error("步骤：查询灵巧手连接状态, 状态为未连接");
    return;
  }
  std::cout << "步骤: 查询灵巧手连接状态, 状态为已连接" << std::endl;
  // 步骤：查询 SDK 版本和固件版本号
  std::string sdk_version = dexh13->getSDKVersion();
  std::string firmware_version = dexh13->getFirmwareVersion();
  std::cout << "步骤: 查询版本号, SDK 版本为 " << sdk_version << ", 固件版本为 " << firmware_version << std::endl;
  // 步骤：断开灵巧手连接
  int is_success = dexh13->disconnectHandy();
  std::cout << (is_success ? "步骤: 断开灵巧手连接, 结果为成功" : "断开灵巧手连接, 结果为失败") << std::endl;
}

void DexH13Example::setJointAngle() {
  std::shared_ptr<paxini::bot::dexh13::DexH13Control> dexh13 = std::make_shared<paxini::bot::dexh13::DexH13Control>();
  // 步骤：激活灵巧手
  int handy_type = dexh13->activeHandy(this->hand_port_num, this->camera_port_num);
  std::cout << "步骤: 激活灵巧手成功, 灵巧手类型为" << (handy_type == 1 ? "左手" : "右手") << std::endl;
  // 步骤：查询灵巧手连接状态
  bool is_online = dexh13->isConnectHandy();
  if (!is_online) {
    throw std::runtime_error("步骤：查询灵巧手连接状态, 状态为未连接");
    return;
  }
  std::cout << "步骤: 查询灵巧手连接状态, 状态为已连接" << std::endl;
  // 步骤：初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
  int is_success = dexh13->initMotorPosition();
  std::cout << "步骤: 初始化电机位置, 结果为" << (is_success ? "成功" : "失败") << std::endl;
  // 步骤：设置控制模式-位置模式
  bool is_true = dexh13->setMotorControlMode(paxini::bot::dexh13::ControlMode::POSITION_CONTROL_MODE);
  std::cout << "步骤: 设置控制模式(位置模式), 结果为" << (is_true ? "成功" : "失败") << std::endl;
  // 设置控制模式失败，退出
  if (!is_true) {
    dexh13->disconnectHandy();
    return;
  }
  // 步骤：获取控制模式
  paxini::bot::dexh13::ControlMode control_mode = dexh13->getMotorControlMode();
  std::cout << "步骤: 查询控制模式, 控制模式为 " << std::to_string(control_mode) << std::endl;
  // 步骤：使能手指电机
  try {
    is_true = dexh13->enableMotor();
  } catch (...) {
    dexh13->disconnectHandy();
    throw std::runtime_error("使能灵巧手失败，无法设置关节角度");
    return;
  }
  std::cout << "步骤: 使能电机, 结果为" << (is_true ? "成功" : "失败") << std::endl;
  // 步骤：检查电机使能状态
  is_true = dexh13->isMotorEnabled();
  std::cout << "步骤: 查询电机使能状态, 状态为" << (is_true ? "已使能" : "未使能") << std::endl;
  // 如果电机未使能，退出
  if (!is_true) {
    dexh13->disconnectHandy();
    throw std::runtime_error("电机未使能，无法设置关节角度");
    return;
  }
  // 步骤：设置关节角度
  std::vector<paxini::bot::dexh13::FingerAngle> angles = {
      {10.0, 50.0, 50.0, 50.0},  // 食指 indexFinger
      {10.0, 50.0, 50.0, 50.0},  // 中指 middleFinger
      {10.0, 50.0, 50.0, 50.0},  // 环指 ringFinger
      {10.0, 50.0, 50.0, 50.0},  // 大拇指 thumb
  };
  is_success = dexh13->setJointPositionsAngle(angles);
  std::cout << "步骤: 设置关节角度, 结果为" << (is_success ? "成功" : "失败") << std::endl;
  std::this_thread::sleep_for(std::chrono::seconds(2));
  // 步骤：获取关节角度
  std::vector<paxini::bot::dexh13::FingerAngle> target_angles = {};
  dexh13->getJointPositionsAngle(target_angles);
  std::cout << "步骤: 获取关节角度, 角度为" << std::endl;
  this->printJointAngle(target_angles);
  std::this_thread::sleep_for(std::chrono::seconds(1));
  // 步骤：查询手指故障状态(可选)
  is_true = dexh13->isFault();
  std::cout << "步骤: 查询手指故障状态, 状态为" << (is_true ? "有故障" : "无故障") << std::endl;
  // 步骤：查询当前故障原因(可选)
  if (is_true) {
    std::vector<paxini::bot::dexh13::FingerMotorsError> fault_codes = dexh13->getFaultCode();
  }
  // 步骤：清空故障码(可选)
  is_true = dexh13->clearFaultCode();
  std::cout << "步骤: 清空故障码, 结果为" << (is_true ? "成功" : "失败") << std::endl;
  // 步骤：下使能手指电机
  is_true = dexh13->disableMotor();
  std::cout << "步骤: 下使能, 结果为" << (is_true ? "成功" : "失败") << std::endl;
  // 步骤：断开灵巧手连接
  is_success = dexh13->disconnectHandy();
  std::cout << (is_success ? "步骤: 断开灵巧手连接, 结果为成功" : "断开灵巧手连接, 结果为失败") << std::endl;
}

void DexH13Example::setJointRadian() {
  std::shared_ptr<paxini::bot::dexh13::DexH13Control> dexh13 = std::make_shared<paxini::bot::dexh13::DexH13Control>();
  // 步骤：激活灵巧手
  int handy_type = dexh13->activeHandy(this->hand_port_num, this->camera_port_num);
  std::cout << "步骤: 激活灵巧手成功, 灵巧手类型为" << (handy_type == 1 ? "左手" : "右手") << std::endl;
  // 步骤：查询灵巧手连接状态
  bool is_online = dexh13->isConnectHandy();
  if (!is_online) {
    throw std::runtime_error("步骤：查询灵巧手连接状态, 状态为未连接");
    return;
  }
  std::cout << "步骤: 查询灵巧手连接状态, 状态为已连接" << std::endl;
  // 步骤：初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
  int is_success = dexh13->initMotorPosition();
  std::cout << "步骤: 初始化电机位置, 结果为" << (is_success ? "成功" : "失败") << std::endl;
  // 步骤：设置控制模式-位置模式
  bool is_true = dexh13->setMotorControlMode(paxini::bot::dexh13::ControlMode::POSITION_CONTROL_MODE);
  std::cout << "步骤: 设置控制模式(位置模式), 结果为" << (is_true ? "成功" : "失败") << std::endl;
  // 设置控制模式失败，退出
  if (!is_true) {
    dexh13->disconnectHandy();
    return;
  }
  // 步骤：获取控制模式
  paxini::bot::dexh13::ControlMode control_mode = dexh13->getMotorControlMode();
  std::cout << "步骤: 查询控制模式, 控制模式为 " << std::to_string(control_mode) << std::endl;
  // 步骤：使能手指电机
  try {
    is_true = dexh13->enableMotor();
  } catch (...) {
    dexh13->disconnectHandy();
    throw std::runtime_error("使能灵巧手失败，无法设置关节弧度");
    return;
  }
  std::cout << "步骤: 使能电机, 结果为" << (is_true ? "成功" : "失败") << std::endl;
  // 步骤：检查电机使能状态
  is_true = dexh13->isMotorEnabled();
  std::cout << "步骤: 查询电机使能状态, 状态为" << (is_true ? "已使能" : "未使能") << std::endl;
  // 如果电机未使能，退出
  if (!is_true) {
    dexh13->disconnectHandy();
    throw std::runtime_error("电机未使能，无法设置关节弧度");
    return;
  }
  // 步骤：设置关节弧度
  std::vector<double> radians = {
      M_PI / 180 * 10.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0,   // 食指 indexFinger
      M_PI / 180 * 10.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0,   // 中指 middleFinger
      M_PI / 180 * 10.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0,   // 环指 ringFinger
      M_PI / 180 * 10.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0};  // 大拇指 thumb
  is_success = dexh13->setJointPositionsRadian(radians);
  std::cout << "步骤: 设置关节弧度, 结果为" << (is_success ? "成功" : "失败") << std::endl;
  std::this_thread::sleep_for(std::chrono::seconds(2));
  // 步骤：获取关节弧度
  std::vector<double> target_radians;
  dexh13->getJointPositionsRadian(target_radians);
  std::cout << "步骤: 获取关节弧度, 弧度为" << std::endl;
  this->printJointRadians(target_radians);
  std::this_thread::sleep_for(std::chrono::seconds(1));
  // 步骤：查询手指故障状态(可选)
  is_true = dexh13->isFault();
  std::cout << "步骤: 查询手指故障状态, 状态为" << (is_true ? "有故障" : "无故障") << std::endl;
  // 步骤：查询当前故障原因(可选)
  if (is_true) {
    std::vector<paxini::bot::dexh13::FingerMotorsError> fault_codes = dexh13->getFaultCode();
  }
  // 步骤：清空故障码(可选)
  is_true = dexh13->clearFaultCode();
  std::cout << "步骤: 清空故障码, 结果为" << (is_true ? "成功" : "失败") << std::endl;
  // 步骤：下使能手指电机
  is_true = dexh13->disableMotor();
  std::cout << "步骤: 下使能, 结果为" << (is_true ? "成功" : "失败") << std::endl;
  // 步骤：断开灵巧手连接
  is_success = dexh13->disconnectHandy();
  std::cout << (is_success ? "步骤: 断开灵巧手连接, 结果为成功" : "断开灵巧手连接, 结果为失败") << std::endl;
}

void DexH13Example::runGestures() {
  std::shared_ptr<paxini::bot::dexh13::DexH13Control> dexh13 = std::make_shared<paxini::bot::dexh13::DexH13Control>();
  // 步骤：激活灵巧手
  int handy_type = dexh13->activeHandy(this->hand_port_num, this->camera_port_num);
  std::cout << "步骤: 激活灵巧手成功, 灵巧手类型为" << (handy_type == 1 ? "左手" : "右手") << std::endl;
  // 步骤：查询灵巧手连接状态
  bool is_online = dexh13->isConnectHandy();
  if (!is_online) {
    throw std::runtime_error("步骤：查询灵巧手连接状态, 状态为未连接");
    return;
  }
  std::cout << "步骤: 查询灵巧手连接状态, 状态为已连接" << std::endl;
  // 步骤：初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
  int is_success = dexh13->initMotorPosition();
  std::cout << "步骤: 初始化电机位置, 结果为" << (is_success ? "成功" : "失败") << std::endl;
  // 步骤：设置控制模式-位置模式
  bool is_true = dexh13->setMotorControlMode(paxini::bot::dexh13::ControlMode::POSITION_CONTROL_MODE);
  std::cout << "步骤: 设置控制模式(位置模式), 结果为" << (is_true ? "成功" : "失败") << std::endl;
  // 设置控制模式失败，退出
  if (!is_true) {
    dexh13->disconnectHandy();
    return;
  }
  // 步骤：获取控制模式
  paxini::bot::dexh13::ControlMode control_mode = dexh13->getMotorControlMode();
  std::cout << "步骤: 查询控制模式, 控制模式为 " << std::to_string(control_mode) << std::endl;
  // 步骤：使能手指电机
  try {
    is_true = dexh13->enableMotor();
  } catch (...) {
    dexh13->disconnectHandy();
    throw std::runtime_error("使能灵巧手失败，无法执行手势");
    return;
  }
  std::cout << "步骤: 使能电机, 结果为" << (is_true ? "成功" : "失败") << std::endl;
  // 步骤：检查电机使能状态
  is_true = dexh13->isMotorEnabled();
  std::cout << "步骤: 查询电机使能状态, 状态为" << (is_true ? "已使能" : "未使能") << std::endl;
  // 如果电机未使能，退出
  if (!is_true) {
    dexh13->disconnectHandy();
    throw std::runtime_error("电机未使能，无法执行手势");
    return;
  }
  // 零位
  std::vector<paxini::bot::dexh13::FingerAngle> zero_angles = {{0, 0, 0, 0}, {0, 0, 0, 0}, {0, 0, 0, 0}, {0, 0, 0, 0}};
  // 手势列表
  std::vector<std::vector<paxini::bot::dexh13::FingerAngle>> angles_list = {
      {{10, 0, 0, 0}, {-10, 0, 0, 0}, {-20, 85, 45, 0}, {-20, 0, 55, 70}},  // 手势比v
      {{0, 55, 30, 0}, {0, 0, 0, 0}, {0, 0, 0, 0}, {0, 80, 30, 70}},        // 手势 OK
      {{0, 80, 70, 0}, {0, 80, 70, 0}, {0, 80, 70, 0}, {0, 0, 80, 50}},     // 手势握拳
  };
  size_t index = 0;
  while (true) {
    // 执行手势
    std::vector<paxini::bot::dexh13::FingerAngle> angles = angles_list[index];
    is_success = dexh13->setJointPositionsAngle(angles);
    this->printJointAngle(angles);
    std::cout << "步骤: 执行手势, 结果为" << (is_success ? "成功" : "失败") << std::endl;
    std::this_thread::sleep_for(std::chrono::seconds(2));
    // 获取手势角度
    std::vector<paxini::bot::dexh13::FingerAngle> target_angles;
    dexh13->getJointPositionsAngle(target_angles);
    std::cout << "步骤: 获取手势角度, 结果为" << std::endl;
    this->printJointAngle(target_angles);
    std::this_thread::sleep_for(std::chrono::seconds(2));
    // 回零
    is_success = dexh13->setJointPositionsAngle(zero_angles);
    std::cout << "步骤: 关节回零, 结果为" << (is_success ? "成功" : "失败") << std::endl;
    index = (index + 1) % angles_list.size();
  }
}

void DexH13Example::runSpeedControlMode() {
  std::shared_ptr<paxini::bot::dexh13::DexH13Control> dexh13 = std::make_shared<paxini::bot::dexh13::DexH13Control>();
  // 步骤：激活灵巧手
  int handy_type = dexh13->activeHandy(this->hand_port_num, this->camera_port_num);
  std::cout << "步骤: 激活灵巧手成功, 灵巧手类型为" << (handy_type == 1 ? "左手" : "右手") << std::endl;
  // 步骤：查询灵巧手连接状态
  bool is_online = dexh13->isConnectHandy();
  if (!is_online) {
    throw std::runtime_error("步骤：查询灵巧手连接状态, 状态为未连接");
    return;
  }
  std::cout << "步骤: 查询灵巧手连接状态, 状态为已连接" << std::endl;
  // 步骤：初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
  int is_success = dexh13->initMotorPosition();
  std::cout << "步骤: 初始化电机位置, 结果为" << (is_success ? "成功" : "失败") << std::endl;
  // 步骤：设置控制模式-速度模式
  bool is_true = dexh13->setMotorControlMode(paxini::bot::dexh13::ControlMode::SPEED_CONTROL_MODE);
  std::cout << "步骤: 设置控制模式(速度模式), 结果为" << (is_true ? "成功" : "失败") << std::endl;
  // 设置控制模式失败，退出
  if (!is_true) {
    dexh13->disconnectHandy();
    return;
  }
  // 步骤：获取控制模式
  paxini::bot::dexh13::ControlMode control_mode = dexh13->getMotorControlMode();
  std::cout << "步骤: 查询控制模式, 控制模式为 " << std::to_string(control_mode) << std::endl;
  // 步骤：使能手指电机
  try {
    is_true = dexh13->enableMotor();
  } catch (...) {
    dexh13->disconnectHandy();
    throw std::runtime_error("使能灵巧手失败，无法执行速度模式");
    return;
  }
  std::cout << "步骤: 使能电机, 结果为" << (is_true ? "成功" : "失败") << std::endl;
  // 步骤：检查电机使能状态
  is_true = dexh13->isMotorEnabled();
  std::cout << "步骤: 查询电机使能状态, 状态为" << (is_true ? "已使能" : "未使能") << std::endl;
  // 如果电机未使能，退出
  if (!is_true) {
    dexh13->disconnectHandy();
    throw std::runtime_error("电机未使能，无法执行速度模式");
    return;
  }
  // 步骤：设置电机的目标速度
  paxini::bot::dexh13::Dex13MotorSpeed speed{0, 0, 2000, 0, 0, 2000, 0, 0, 2000, 0, 0, 2000, 2000};
  is_success = dexh13->setMotorTargetSpeed(speed);
  std::cout << "步骤: 设置电机的目标速度, 结果为" << (is_success ? "成功" : "失败") << std::endl;
  std::this_thread::sleep_for(std::chrono::seconds(4));
  // 步骤：查询手指故障状态(可选)
  is_true = dexh13->isFault();
  std::cout << "步骤: 查询手指故障状态, 状态为" << (is_true ? "有故障" : "无故障") << std::endl;
  // 步骤：查询当前故障原因(可选)
  if (is_true) {
    std::vector<paxini::bot::dexh13::FingerMotorsError> fault_codes = dexh13->getFaultCode();
  }
  // 步骤：清空故障码(可选)
  is_true = dexh13->clearFaultCode();
  std::cout << "步骤: 清空故障码, 结果为" << (is_true ? "成功" : "失败") << std::endl;
  // 步骤：下使能手指电机
  is_true = dexh13->disableMotor();
  std::cout << "步骤: 下使能, 结果为" << (is_true ? "成功" : "失败") << std::endl;
  // 步骤：断开灵巧手连接
  is_success = dexh13->disconnectHandy();
  std::cout << (is_success ? "步骤: 断开灵巧手连接, 结果为成功" : "断开灵巧手连接, 结果为失败") << std::endl;
}

void DexH13Example::getFingerTactile() {
  std::shared_ptr<paxini::bot::dexh13::DexH13Control> dexh13 = std::make_shared<paxini::bot::dexh13::DexH13Control>();
  // 步骤：激活灵巧手
  int handy_type = dexh13->activeHandy(this->hand_port_num, this->camera_port_num);
  std::cout << "步骤: 激活灵巧手成功，灵巧手类型为" << (handy_type == 1 ? "左手" : "右手") << std::endl;
  // 步骤：查询灵巧手连接状态
  bool is_online = dexh13->isConnectHandy();
  if (!is_online) {
    throw std::runtime_error("灵巧手未连接");
    return;
  }
  std::cout << "步骤: 灵巧手已连接" << std::endl;
  // 步骤：获取传感器数据(按住指尖传感器,数值才有变化)
  int hz = 10;
  while (true) {
    std::vector<paxini::bot::dexh13::ForcePoint> force_points = dexh13->getFingerTactile();
    this->printForcePoints(force_points);
    std::this_thread::sleep_for(std::chrono::milliseconds(static_cast<int>(1000.0 / hz)));
  }
}

void DexH13Example::undistortImage() {
  std::shared_ptr<paxini::bot::dexh13::DexH13Control> dexh13 = std::make_shared<paxini::bot::dexh13::DexH13Control>();
  // 步骤：激活灵巧手
  int handy_type = dexh13->activeHandy(this->hand_port_num, this->camera_port_num);
  std::cout << "步骤: 激活灵巧手成功, 灵巧手类型为" << (handy_type == 1 ? "左手" : "右手") << std::endl;
  // 步骤：查询灵巧手连接状态
  bool is_online = dexh13->isConnectHandy();
  if (!is_online) {
    throw std::runtime_error("步骤：查询灵巧手连接状态, 状态为未连接");
    return;
  }
  std::cout << "步骤: 查询灵巧手连接状态, 状态为已连接" << std::endl;
  // 步骤：获取相机内参矩阵和畸变系数(分辨率为 640x480)
  std::pair<cv::Mat, cv::Mat> intrinsic_matrix_and_dist_coeffs = dexh13->getIntrinsicMatrixAndDistCoeffs(640, 480);
  std::cout << "相机内参矩阵:" << intrinsic_matrix_and_dist_coeffs.first << std::endl;
  std::cout << "畸变系数:" << intrinsic_matrix_and_dist_coeffs.second << std::endl;
  // 步骤：获取相机外参矩阵
  cv::Mat hand_eye_matrix = dexh13->getHandEyeMatrix();
  std::cout << "相机外参矩阵:" << hand_eye_matrix << std::endl;
  // 步骤：设置相机分辨率和帧率(分辨率为 640x480, 帧率为 30 fps)
  dexh13->setCameraConfig(640, 480, 30);
  // 步骤：获取当前帧功能
  cv::Mat original_image = dexh13->getFrame();
  // 步骤：保存一帧图片
  dexh13->saveImage("save_orig_image.png", original_image);
  // 步骤：图像校正（去畸变）
  cv::Mat undistorted_image = dexh13->undistortImage(original_image, intrinsic_matrix_and_dist_coeffs.first,
                                                     intrinsic_matrix_and_dist_coeffs.second);
  // 步骤：保存校正后的图像
  dexh13->saveImage("save_undistorted_image.png", undistorted_image);
  while (true) {
    cv::Mat ori_frame = dexh13->getFrame();
    cv::imshow("Original Image", ori_frame);
    cv::Mat undistort_frame = dexh13->undistortImage(ori_frame, intrinsic_matrix_and_dist_coeffs.first,
                                                     intrinsic_matrix_and_dist_coeffs.second);
    cv::imshow("Undistorted Image", undistort_frame);
    if (cv::waitKey(1) == 27) {
      break;
    }
  }
}

void DexH13Example::forwardKinematicParse() {
  std::shared_ptr<paxini::bot::dexh13::DexH13Kinematic> dexh13_kinematic =
      std::make_shared<paxini::bot::dexh13::DexH13Kinematic>();
  // 电机实际位置值集合
  std::vector<int16_t> motor_pos = {2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 100, 100, 2000, 1000};

  // 解析获取对应的实际手指弧度集合
  auto hand_type = paxini::bot::dexh13::HandType::LEFT_HAND;  // 请根据左右手类型选择，这里以左手为例
  std::vector<double> target_radians;
  dexh13_kinematic->forwardKinematicParse(hand_type, motor_pos, target_radians);

  std::cout << "正运动学解析得到的弧度为" << std::endl;
  this->printJointRadians(target_radians);
}

void DexH13Example::inverseKinematicParse() {
  std::shared_ptr<paxini::bot::dexh13::DexH13Kinematic> dexh13_kinematic =
      std::make_shared<paxini::bot::dexh13::DexH13Kinematic>();
  // 需要解析的弧度集合
  std::vector<double> radians = {
      M_PI / 180 * 10.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0,   // 食指 indexFinger
      M_PI / 180 * 10.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0,   // 中指 middleFinger
      M_PI / 180 * 10.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0,   // 环指 ringFinger
      M_PI / 180 * 10.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0, M_PI / 180 * 50.0};  // 大拇指 thumb

  // 解析获取对应的实际电机位置集合
  auto hand_type = paxini::bot::dexh13::HandType::LEFT_HAND;  // 请根据左右手类型选择，这里以左手为例
  std::vector<int16_t> arm_pos;
  dexh13_kinematic->inverseKinematicParse(hand_type, radians, arm_pos);
  for (int i = 0; i < arm_pos.size(); i++) {
    std::cout << "motor " << i + 1 << " : " << arm_pos[i] << std::endl;
  }
}

void DexH13Example::printJointAngle(const std::vector<paxini::bot::dexh13::FingerAngle>& joint_angles) {
  std::ostringstream oss;
  oss << "[";
  for (int i = 0; i < 4; i++) {
    oss << "[" << joint_angles[i].joint1 << "," << joint_angles[i].joint2 << "," << joint_angles[i].joint3 << ","
        << joint_angles[i].joint4 << "]";
  }
  oss << "]";
  std::cout << oss.str() << std::endl;
}

void DexH13Example::printJointRadians(const std::vector<double>& joint_radians) {
  std::ostringstream oss;
  oss << "[";
  for (const auto& joint_radian : joint_radians) {
    oss << joint_radian << ",";
  }
  std::string result = oss.str();
  result = oss.str();
  result = result.substr(0, result.size() - 1);
  result += "]";
  std::cout << result << std::endl;
}

std::string DexH13Example::getErrorCodeName(paxini::bot::dexh13::ErrorCode error_code) {
  switch (error_code) {
    case paxini::bot::dexh13::ErrorCode::OVER_VOLT:
      return "OVER_VOLT";
    case paxini::bot::dexh13::ErrorCode::UNDER_VOLT:
      return "UNDER_VOLT";
    case paxini::bot::dexh13::ErrorCode::OVER_CURRENTU:
      return "OVER_CURRENTU";
    case paxini::bot::dexh13::ErrorCode::OVER_CURRENTV:
      return "OVER_CURRENTV";
    case paxini::bot::dexh13::ErrorCode::OVER_CURRENTW:
      return "OVER_CURRENTW";
    case paxini::bot::dexh13::ErrorCode::OVER_TEMPERATURE:
      return "OVER_TEMPERATURE";
    case paxini::bot::dexh13::ErrorCode::ADC_CALIB:
      return "ADC_CALIB";
    case paxini::bot::dexh13::ErrorCode::FOC_ERR:
      return "FOC_ERR";
    case paxini::bot::dexh13::ErrorCode::OVER_IQ:
      return "OVER_IQ";
    case paxini::bot::dexh13::ErrorCode::OPD_U:
      return "OPD_U";
    case paxini::bot::dexh13::ErrorCode::OPD_V:
      return "OPD_V";
    case paxini::bot::dexh13::ErrorCode::OPD_W:
      return "OPD_W";
    case paxini::bot::dexh13::ErrorCode::POS_REF_OVER:
      return "POS_REF_OVER";
    case paxini::bot::dexh13::ErrorCode::POS_REF_UNDER:
      return "POS_REF_UNDER";
    case paxini::bot::dexh13::ErrorCode::ABZ_ERR:
      return "ABZ_ERR";
    case paxini::bot::dexh13::ErrorCode::ABZ_Z_ERR:
      return "ABZ_Z_ERR";
    case paxini::bot::dexh13::ErrorCode::DISCONNECT:
      return "DISCONNECT";
    default:
      return "UNKNOWN";
  }
}

void DexH13Example::printFingerErrorCodes(
    const std::vector<paxini::bot::dexh13::FingerMotorsError>& finger_error_codes) {
  for (auto finger_error_code : finger_error_codes) {
    std::cout << "fingler name:" << int(finger_error_code.motor_ID) << ", motor id:" << int(finger_error_code.motor_ID)
              << ", error code " << this->getErrorCodeName(finger_error_code.error_code) << std::endl;
  }
}

void DexH13Example::printForcePoints(const std::vector<paxini::bot::dexh13::ForcePoint>& force_points) {
  for (auto force_point : force_points) {
    std::cout << "x:" << force_point.x << ", y:" << force_point.y << ", z:" << force_point.z << std::endl;
  }
}