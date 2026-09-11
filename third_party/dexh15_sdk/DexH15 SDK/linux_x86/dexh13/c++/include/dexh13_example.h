/**
 ******************************************************************************
 * @file: dexh13_example.h
 * @brief: DexH13 SDK 示例
 * @version: 0.0.1
 * @date: 2025-07-02
 * @copyright: Copyright (c) 2025 Paxini Robotics
 * @author: paxini
 * @details:
 ******************************************************************************
 */
#pragma once
#include <dexh13/dexh13_control.h>
#include <dexh13/dexh13_define.h>
#include <dexh13/dexh13_kinematic.h>

#include <string>

/**
 * @class Dexh13Example
 * @brief DexH13 SDK 关键功能的使用教程示例
 * @note
 * @see
 */
class DexH13Example {
 public:
  DexH13Example(const std::string& hand_port_num, const std::string& camera_port_num);
  ~DexH13Example();

  /**
   * @brief 查询 DexH13 SDK 的版本信息
   * @details DexH13 SDK 的版本信息包括 SDK 的软件版本信息和固件版本信息
   *          步骤：
   *          (1) 激活灵巧手
   *          (2) 查询灵巧手连接状态
   *          (3) 查询 SDK 版本
   *          (4) 查询固件版本
   *          (5) 断开灵巧手连接
   * @return void
   */
  void getVersion();

  /**
   * @brief 控制关节角度(位置模式)
   * @details 步骤：
   *          (1) 激活灵巧手
   *          (2) 查询灵巧手连接状态
   *          (3) 初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
   *          (4) 设置控制模式-位置模式
   *          (5) 获取控制模式
   *          (6) 使能手指电机
   *          (7) 检查电机使能状态
   *          (8) 设置关节角度
   *          (9) 获取关节角度
   *          (10)查询手指故障状态(可选)
   *          (11)查询当前故障原因(可选)
   *          (12)清空故障码(可选)
   *          (13)下使能手指电机
   *          (14)断开灵巧手连接
   * @return void
   */
  void setJointAngle();

  /**
   * @brief 控制关节弧度(位置模式)
   * @details 步骤：
   *          (1) 激活灵巧手
   *          (2) 查询灵巧手连接状态
   *          (3) 初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
   *          (4) 设置控制模式-位置模式
   *          (5) 获取控制模式
   *          (6) 使能手指电机
   *          (7) 检查电机使能状态
   *          (8) 设置关节弧度
   *          (9) 获取关节弧度
   *          (10)查询手指故障状态(可选)
   *          (11)查询当前故障原因(可选)
   *          (12)清空故障码(可选)
   *          (13)下使能手指电机
   *          (14)断开灵巧手连接
   * @return void
   */
  void setJointRadian();

  /**
   * @brief 获取传感器数据
   * @details 步骤：
   *          (1) 激活灵巧手
   *          (2) 查询灵巧手连接状态
   *          (3) 获取传感器信息(按住指尖传感器，数值才有变化)
   *          (4) 断开灵巧手连接
   *          PS：example 中采用死循环来持续获取传感器信息，第(4)步没在 example 中体现
   *              如果要中断 example 的执行，请在命令行窗口按 ctrl + C
   * @return void
   */
  void getFingerTactile();

  /**
   * @brief 校正图像
   * @details 步骤：
   *          (1) 激活灵巧手
   *          (2) 查询灵巧手连接状态
   *          (3) 获取相机内参矩阵和畸变系数(分辨率为640x480)
   *          (4) 获取相机外参矩阵
   *          (5) 设置相机分辨率和帧率(分辨率为 640x480, 帧率为30fps)
   *          (6) 获取当前帧功能
   *          (7) 保存一帧图片
   *          (8) 图像校正（去畸变）
   *          (9) 保存校正后的图像
   *          (10)断开灵巧手连接
   *          PS：example 中采用死循环来持续展示校正图像的效果，第(10)步没在 example 中体现
   *              如果要中断 example 的执行，请在命令行窗口按 ctrl + C
   * @return void
   */
  void undistortImage();

  /**
   * @brief 执行多个手势
   * @details 一共有 3 个手势，分别是比v、OK、握拳
   * @return void
   */
  void runGestures();

  /**
   * @brief 执行速度控制模式
   * @details 步骤：
   *          (1) 激活灵巧手
   *          (2) 查询灵巧手连接状态
   *          (3) 初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
   *          (4) 设置控制模式-速度模式
   *          (5) 获取控制模式
   *          (6) 设置电机最大速度
   *          (7) 使能手指电机
   *          (8) 检查电机使能状态
   *          (9) 设置电机目标速度（执行完后，建议2s等待动作执行完）
   *          (10)查询手指故障状态(可选)
   *          (11)查询当前故障原因(可选)
   *          (12)清空故障码(可选)
   *          (13)下使能手指电机
   *          (14)断开灵巧手连接
   * @return void
   */
  void runSpeedControlMode();

  /**
   * @brief 正运动学解析
   * @details 无
   * @return void
   */
  void forwardKinematicParse();

  /**
   * @brief 逆运动学解析
   * @details 无
   * @return void
   */
  void inverseKinematicParse();

  /**
   * @brief 打印关节角度
   * @details DexH13 手指关节数组，包含 4 个手指关节组.
   *          顺序为：食指 indexFinger，中指 middleFinger，环指 ringFinger，大拇指 thumb;
   *          每个手指（含 4 个关节）的关节名。
   *          限位空间:
   *                  joint1 [-20°, +20°]（掌指双轴关节-侧位摆动范围）
   *                  joint2 [0°, 90°] (掌指双轴关节-弯曲伸展范围)
   *                  joint3 [0°, 90°](近端关节-弯曲伸展范围)
   *                  joint4 [0°, 90°] (末端关节-弯曲伸展范围)
   * @return void
   */
  void printJointAngle(const std::vector<paxini::bot::dexh13::FingerAngle>& joint_angles);

  /**
   * @brief 打印关节弧度
   * @details
   *
   * @return void
   */
  void printJointRadians(const std::vector<double>& joint_radians);

  /**
   * @brief 获取错误码的名称
   * @details DexH13 错误码共有 17 个
   *
   * @return 错误码
   */
  std::string getErrorCodeName(paxini::bot::dexh13::ErrorCode error_code);

  /**
   * @brief 打印手指电机错误玛
   *
   * @return void
   */
  void printFingerErrorCodes(const std::vector<paxini::bot::dexh13::FingerMotorsError>& finger_error_codes);

  /**
   * @brief 打印触觉传感器信息
   * @details 触觉传感器信息包含三个维度的力： x, y, z
   *          x:x轴方向的力
   *          y:y轴方向的力
   *          z:z轴方向的力
   * @return void
   */
  void printForcePoints(const std::vector<paxini::bot::dexh13::ForcePoint>& force_points);

 private:
  /**
   * 灵巧手串口，例如 /dev/ttyUSB*
   * 可用命令 ls /dev/ttyUSB*，来查看
   */
  std::string hand_port_num;
  /**
   * 摄像头串口，例如 /dev/video*
   * 可用命令 ls /dev/video*，来查看
   */
  std::string camera_port_num;
};