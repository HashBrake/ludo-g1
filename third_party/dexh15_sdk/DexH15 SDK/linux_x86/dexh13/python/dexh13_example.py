import math
import time
from typing import List

import cv2
from pxdex.dh13 import ControlMode, FingerAngle, FingerMotorsError, Dex13MotorSpeed
from pxdex.dh13 import DexH13Control


class DexH13Example:
    """
    DexH13Example
    """

    def __init__(self,
                 handy_port_num: str,
                 camera_port_num: str) -> None:
        """
        初始化 DexH13Example 实例
        :param handy_port_num:灵巧手串口, 例如 /dev/ttyUSB*, 可用命令 ls /dev/ttyUSB*，来查看
        :param camera_port_num:摄像头串口, 例如 /dev/video*, 可用命令 ls /dev/video*，来查看
        """
        self.handy_port_num = handy_port_num
        self.camera_port_num = camera_port_num
        self.control = DexH13Control()

    def __create_finger_angle(self, joint1: float, joint2: float, joint3: float, joint4: float) -> FingerAngle:
        """
        创建 FingerAngle 实例
        :param joint1:
        :param joint2:
        :param joint3:
        :param joint4:
        :return: FingerAngle
        """
        finger_angle = FingerAngle()
        finger_angle.joint1 = joint1
        finger_angle.joint2 = joint2
        finger_angle.joint3 = joint3
        finger_angle.joint4 = joint4
        return finger_angle

    def __create_motor_speed(self, speeds: List[int]) -> Dex13MotorSpeed:
        """
        创建 Dex13MotorSpeed 实例
        :param speeds:
        :return: Dex13MotorSpeed
        """
        motor_speed = Dex13MotorSpeed()
        motor_speed.motor1_speed = speeds[0]
        motor_speed.motor2_speed = speeds[1]
        motor_speed.motor3_speed = speeds[2]
        motor_speed.motor4_speed = speeds[3]
        motor_speed.motor5_speed = speeds[4]
        motor_speed.motor6_speed = speeds[5]
        motor_speed.motor7_speed = speeds[6]
        motor_speed.motor8_speed = speeds[7]
        motor_speed.motor9_speed = speeds[8]
        motor_speed.motor10_speed = speeds[9]
        motor_speed.motor11_speed = speeds[10]
        motor_speed.motor12_speed = speeds[11]
        motor_speed.motor13_speed = speeds[12]
        return motor_speed

    def __finger_angles_to_str(self, finger_angles: List[FingerAngle]) -> str:
        """
        FingerAngle 转 str
        :param finger_angles:
        :return
        """
        result = "["
        for finger_angle in finger_angles:
            result += f"[{finger_angle.joint1},{finger_angle.joint2},{finger_angle.joint3},{finger_angle.joint4}]"
        result += "]"
        return result

    def get_version(self) -> None:
        """
        查询 DexH13 SDK 的版本信息
        DexH13 SDK 的版本信息包括 SDK 的软件版本信息和固件版本信息
        步骤：
        (1) 激活灵巧手
        (2) 查询灵巧手连接状态
        (3) 查询 SDK 版本
        (4) 查询固件版本
        (5) 断开灵巧手连接
        :return: None
        """
        # 步骤:激活灵巧手
        handy_type = self.control.activeHandy(self.handy_port_num, self.camera_port_num)
        print(f"步骤: 激活灵巧手成功，灵巧手类型为{'左手' if handy_type == 1 else '右手'}")
        # 步骤:查询灵巧手连接状态
        is_online = self.control.isConnectHandy()
        if not is_online:
            print("灵巧手未连接")
            return
        # 步骤:查询 SDK 版本
        sdk_version = self.control.getSDKVersion()
        print(f"步骤:SDK 版本为{sdk_version}")
        # 步骤:查询固件版本
        firmware_version = self.control.getFirmwareVersion()
        print(f"步骤:固件版本为{firmware_version}")
        # 步骤:断开灵巧手连接
        is_success = self.control.disconnectHandy()
        print(f"步骤:断开灵巧手连接{'成功' if is_success else '失败'}")

    def set_joint_angle(self) -> None:
        """
        控制关节角度位置模式)
        步骤：
        (1) 激活灵巧手
        (2) 查询灵巧手连接状态
        (3) 初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
        (4) 设置控制模式-位置模式
        (5) 获取控制模式
        (6) 使能手指电机
        (7) 检查电机使能状态
        (8) 设置关节角度
        (9) 获取关节角度
        (10)查询手指故障状态(可选)
        (11)查询当前故障原因(可选)
        (12)清空故障码(可选)
        (13)下使能手指电机
        (14)断开灵巧手连接
        :return: None
        """
        # 步骤:激活灵巧手
        handy_type = self.control.activeHandy(self.handy_port_num, self.camera_port_num)
        print(f"步骤: 激活灵巧手成功，灵巧手类型为{'左手' if handy_type == 1 else '右手'}")
        # 步骤:查询灵巧手连接状态
        is_online = self.control.isConnectHandy()
        if not is_online:
            print("灵巧手未连接")
            return
        # 步骤:初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
        is_success = self.control.initMotorPosition()
        print(f"步骤: 初始化电机位置{'成功' if is_success == 1 else '失败'}")
        # 步骤:设置控制模式-位置模式
        is_success = self.control.setMotorControlMode(ControlMode.POSITION_CONTROL_MODE)
        print(f"步骤: 设置控制模式(位置模式){'成功' if is_success else '失败'}")
        # 设置控制模式失败，退出
        if not is_success:
            self.control.disconnectHandy()
            return
        # 步骤:获取控制模式
        control_mode = self.control.getMotorControlMode()
        print(f"步骤: 查询控制模式为{control_mode}")
        # 步骤:使能手指电机
        try:
            is_success = self.control.enableMotor()
            print(f"步骤: 使能电机{'成功' if is_success else '失败'}")
        except Exception as e:
            print(e)
            self.control.disconnectHandy()
            return
        # 步骤:检查电机使能状态
        is_enabled = self.control.isMotorEnabled()
        print(f"步骤: 电机使能状态为{'已使能' if is_enabled else '未使能'}")
        # 如果电机未使能，退出
        if not is_enabled:
            self.control.disconnectHandy()
            return
        # 步骤:设置关节角度
        angles: List[FingerAngle] = [self.__create_finger_angle(10.0, 50.0, 50.0, 50.0),
                                     self.__create_finger_angle(10.0, 50.0, 50.0, 50.0),
                                     self.__create_finger_angle(10.0, 50.0, 50.0, 50.0),
                                     self.__create_finger_angle(10.0, 50.0, 50.0, 50.0)]
        is_success = self.control.setJointPositionsAngle(angles)
        print(f"步骤: 设置关节角度{'成功' if is_success else '失败'}")
        time.sleep(2)
        # 步骤:获取关节角度
        target_angles: List[FingerAngle] = self.control.getJointPositionsAngle()
        print(f"步骤: 获取关节角度为{self.__finger_angles_to_str(target_angles)}")
        # 步骤:查询手指故障状态(可选)
        is_fault = self.control.isFault()
        # 步骤:查询当前故障原因(可选)
        if is_fault:
            fault_codes: List[FingerMotorsError] = self.control.getFaultCode()
            print(f"步骤：故障码为{fault_codes}")
        # 步骤：清空故障码(可选)
        is_success = self.control.clearFaultCode()
        print(f"步骤: 清空故障码{'成功' if is_success else '失败'}")
        is_success = self.control.disableMotor()
        print(f"步骤: 下使能{'成功' if is_success else '失败'}")
        # 步骤:断开灵巧手连接
        is_success = self.control.disconnectHandy()
        print(f"步骤:断开灵巧手连接{'成功' if is_success else '失败'}")

    def set_joint_radian(self) -> None:
        """
        控制关节弧度(位置模式)
        步骤：
        (1) 激活灵巧手
        (2) 查询灵巧手连接状态
        (3) 初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
        (4) 设置控制模式-位置模式
        (5) 获取控制模式
        (6) 使能手指电机
        (7) 检查电机使能状态
        (8) 设置关节弧度
        (9) 获取关节弧度
        (10)查询手指故障状态(可选)
        (11)查询当前故障原因(可选)
        (12)清空故障码(可选)
        (13)下使能手指电机
        (14)断开灵巧手连接
        :return: None
        """
        # 步骤:激活灵巧手
        handy_type = self.control.activeHandy(self.handy_port_num, self.camera_port_num)
        print(f"步骤: 激活灵巧手成功，灵巧手类型为{'左手' if handy_type == 1 else '右手'}")
        # 步骤:查询灵巧手连接状态
        is_online = self.control.isConnectHandy()
        if not is_online:
            print("灵巧手未连接")
            return
        # 步骤:初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
        is_success = self.control.initMotorPosition()
        print(f"步骤: 初始化电机位置{'成功' if is_success == 1 else '失败'}")
        # 步骤:设置控制模式-位置模式
        is_success = self.control.setMotorControlMode(ControlMode.POSITION_CONTROL_MODE)
        print(f"步骤: 设置控制模式(位置模式){'成功' if is_success else '失败'}")
        # 设置控制模式失败，退出
        if not is_success:
            self.control.disconnectHandy()
            return
        # 步骤:获取控制模式
        control_mode = self.control.getMotorControlMode()
        print(f"步骤: 查询控制模式为{control_mode}")
        # 步骤:使能手指电机
        try:
            is_success = self.control.enableMotor()
            print(f"步骤: 使能电机{'成功' if is_success else '失败'}")
        except Exception as e:
            print(e)
            self.control.disconnectHandy()
            return
        # 步骤:检查电机使能状态
        is_enabled = self.control.isMotorEnabled()
        print(f"步骤: 电机使能状态为{'已使能' if is_enabled else '未使能'}")
        # 如果电机未使能，退出
        if not is_enabled:
            self.control.disconnectHandy()
            return
        # 步骤:设置关节弧度
        radians = [0, 0, 0, 0, 0, 0, math.pi / 180 * 50.0, 0, 0, 0, math.pi / 180 * 50.0, 0, 0, math.pi / 180 * 85.0, 0, 0]
        is_success = self.control.setJointPositionsRadian(radians)
        print(f"步骤: 设置关节弧度{'成功' if is_success else '失败'}")
        time.sleep(2)
        # 步骤:获取关节弧度
        target_radians = self.control.getJointPositionsRadian()
        print(f"步骤: 获取关节角度为{target_radians}")
        # 步骤:查询手指故障状态(可选)
        is_fault = self.control.isFault()
        # 步骤:查询当前故障原因(可选)
        if is_fault:
            fault_codes: List[FingerMotorsError] = self.control.getFaultCode()
            print(f"步骤：故障码为{fault_codes}")
        # 步骤：清空故障码(可选)
        is_success = self.control.clearFaultCode()
        print(f"步骤: 清空故障码{'成功' if is_success else '失败'}")
        is_success = self.control.disableMotor()
        print(f"步骤: 下使能{'成功' if is_success else '失败'}")
        # 步骤:断开灵巧手连接
        is_success = self.control.disconnectHandy()
        print(f"步骤:断开灵巧手连接{'成功' if is_success else '失败'}")

    def run_gestures(self) -> None:
        """
        执行多个手势
        一共有 3 个手势，分别是比v、OK、握拳
        :return: None
        """
        # 步骤:激活灵巧手
        handy_type = self.control.activeHandy(self.handy_port_num, self.camera_port_num)
        print(f"步骤: 激活灵巧手成功，灵巧手类型为{'左手' if handy_type == 1 else '右手'}")
        # 步骤:查询灵巧手连接状态
        is_online = self.control.isConnectHandy()
        if not is_online:
            print("灵巧手未连接")
            return
        # 步骤:初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
        is_success = self.control.initMotorPosition()
        print(f"步骤: 初始化电机位置{'成功' if is_success == 1 else '失败'}")
        # 步骤:设置控制模式-位置模式
        is_success = self.control.setMotorControlMode(ControlMode.POSITION_CONTROL_MODE)
        print(f"步骤: 设置控制模式(位置模式){'成功' if is_success else '失败'}")
        # 设置控制模式失败，退出
        if not is_success:
            self.control.disconnectHandy()
            return
        # 步骤:获取控制模式
        control_mode = self.control.getMotorControlMode()
        print(f"步骤: 查询控制模式为{control_mode}")
        # 步骤:使能手指电机
        try:
            is_success = self.control.enableMotor()
            print(f"步骤: 使能电机{'成功' if is_success else '失败'}")
        except Exception as e:
            print(e)
            self.control.disconnectHandy()
            return
        # 步骤:检查电机使能状态
        is_enabled = self.control.isMotorEnabled()
        print(f"步骤: 电机使能状态为{'已使能' if is_enabled else '未使能'}")
        # 如果电机未使能，退出
        if not is_enabled:
            self.control.disconnectHandy()
            return
        # 步骤:设置关节角度
        angles_list: List[List[FingerAngle]] = [[self.__create_finger_angle(10, 0, 0, 0),
                                                 self.__create_finger_angle(-10.0, 0.0, 0.0, 0.0),
                                                 self.__create_finger_angle(-20, 85, 45, 0),
                                                 self.__create_finger_angle(-20, 0, 55, 70)],
                                                [self.__create_finger_angle(0, 55, 30, 0),
                                                 self.__create_finger_angle(0, 0, 0, 0),
                                                 self.__create_finger_angle(0, 0, 0, 0),
                                                 self.__create_finger_angle(0, 80, 30, 70)],
                                                [self.__create_finger_angle(0, 80, 70, 0),
                                                 self.__create_finger_angle(0, 80, 70, 0),
                                                 self.__create_finger_angle(0, 80, 70, 0),
                                                 self.__create_finger_angle(0, 0, 80, 50)],
                                                ]
        # 初始位置
        zero_angles: List[FingerAngle] = [self.__create_finger_angle(0, 0, 0, 0),
                                          self.__create_finger_angle(0, 0, 0, 0),
                                          self.__create_finger_angle(0, 0, 0, 0),
                                          self.__create_finger_angle(0, 0, 0, 0)]
        for i in range(len(angles_list)):
            is_success = self.control.setJointPositionsAngle(angles_list[i])
            print(f"步骤: 设置关节角度{'成功' if is_success else '失败'}")
            time.sleep(2)
            # 步骤:获取关节角度
            target_angles: List[FingerAngle] = self.control.getJointPositionsAngle()
            print(f"步骤: 获取关节角度为{self.__finger_angles_to_str(target_angles)}")
            self.control.setJointPositionsAngle(zero_angles)
            time.sleep(2)
        # 步骤:查询手指故障状态(可选)
        is_fault = self.control.isFault()
        # 步骤:查询当前故障原因(可选)
        if is_fault:
            fault_codes: List[FingerMotorsError] = self.control.getFaultCode()
            print(f"步骤：故障码为{fault_codes}")
        # 步骤：清空故障码(可选)
        is_success = self.control.clearFaultCode()
        print(f"步骤: 清空故障码{'成功' if is_success else '失败'}")
        is_success = self.control.disableMotor()
        print(f"步骤: 下使能{'成功' if is_success else '失败'}")
        # 步骤:断开灵巧手连接
        is_success = self.control.disconnectHandy()
        print(f"步骤:断开灵巧手连接{'成功' if is_success else '失败'}")

    def run_speed_control_mode(self) -> None:
        """
        执行速度控制模式
        步骤：
        (1) 激活灵巧手
        (2) 查询灵巧手连接状态
        (3) 初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
        (4) 设置控制模式-速度模式
        (5) 获取控制模式
        (6) 设置电机最大速度
        (7) 使能手指电机
        (8) 检查电机使能状态
        (9) 设置电机目标速度（执行完后，建议2s等待动作执行完）
        (10)查询手指故障状态(可选)
        (11)查询当前故障原因(可选)
        (12)清空故障码(可选)
        (13)下使能手指电机
        (14)断开灵巧手连接
        :return: None
        """
        # 步骤:激活灵巧手
        handy_type = self.control.activeHandy(self.handy_port_num, self.camera_port_num)
        print(f"步骤: 激活灵巧手成功，灵巧手类型为{'左手' if handy_type == 1 else '右手'}")
        # 步骤:查询灵巧手连接状态
        is_online = self.control.isConnectHandy()
        if not is_online:
            print("灵巧手未连接")
            return
        # 步骤:初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
        is_success = self.control.initMotorPosition()
        print(f"步骤: 初始化电机位置{'成功' if is_success == 1 else '失败'}")
        # 步骤:设置控制模式-位置模式
        is_success = self.control.setMotorControlMode(ControlMode.SPEED_CONTROL_MODE)
        print(f"步骤: 设置控制模式(速度模式){'成功' if is_success else '失败'}")
        # 设置控制模式失败，退出
        if not is_success:
            self.control.disconnectHandy()
            return
        # 步骤:获取控制模式
        control_mode = self.control.getMotorControlMode()
        print(f"步骤: 查询控制模式为{control_mode}")
        # 步骤:设置电机最大速度
        is_success = self.control.setMotorMaxSpeed(20000)
        print(f"步骤: 设置电机最大速度{'成功' if is_success else '失败'}")
        # 步骤:使能手指电机
        try:
            is_success = self.control.enableMotor()
            print(f"步骤: 使能电机{'成功' if is_success else '失败'}")
        except Exception as e:
            print(e)
            self.control.disconnectHandy()
            return
        # 步骤:检查电机使能状态
        is_enabled = self.control.isMotorEnabled()
        print(f"步骤: 电机使能状态为{'已使能' if is_enabled else '未使能'}")
        # 如果电机未使能，退出
        if not is_enabled:
            self.control.disconnectHandy()
            return
        # 步骤:设置电机的目标速度
        motor_speed = self.__create_motor_speed([0, 0, 0, 0, 0, 0, 0, 0, 0, 4000, 4000, 0, 0])
        is_success = self.control.setMotorTargetSpeed(motor_speed)

        result = self.control.getJointPositionsRadian()
        print(result)

        print(f"步骤: 设置电机的目标速度{'成功' if is_success == 1 else '失败'}")
        time.sleep(4)
        # 步骤:查询手指故障状态(可选)
        is_fault = self.control.isFault()
        # 步骤:查询当前故障原因(可选)
        if is_fault:
            fault_codes: List[FingerMotorsError] = self.control.getFaultCode()
            print(f"步骤：故障码为{fault_codes}")
        # 步骤：清空故障码(可选)
        is_success = self.control.clearFaultCode()
        print(f"步骤: 清空故障码{'成功' if is_success else '失败'}")
        is_success = self.control.disableMotor()
        print(f"步骤: 下使能{'成功' if is_success else '失败'}")
        # 步骤:断开灵巧手连接
        is_success = self.control.disconnectHandy()
        print(f"步骤: 断开灵巧手连接{'成功' if is_success else '失败'}")

    def get_finger_tactile(self) -> None:
        """
        获取传感器数据
        步骤：
        (1) 激活灵巧手
        (2) 查询灵巧手连接状态
        (3) 获取传感器信息(按住指尖传感器，数值才有变化)
        (4) 断开灵巧手连接
        PS：example 中采用死循环来持续获取传感器信息，第(4)步没在 example 中体现
        如果要中断 example 的执行，请在命令行窗口按 ctrl + C
        :return: None
        """
        # 步骤:激活灵巧手
        handy_type = self.control.activeHandy(self.handy_port_num, self.camera_port_num)
        print(f"步骤: 激活灵巧手成功，灵巧手类型为{'左手' if handy_type == 1 else '右手'}")
        # 步骤:查询灵巧手连接状态
        is_online = self.control.isConnectHandy()
        if not is_online:
            print("灵巧手未连接")
            return
        hz = 10
        # 步骤：获取传感器数据(按住指尖传感器,数值才有变化)
        while True:
            finger_tactile_list = self.control.getFingerTactile()
            for finger_tactile in finger_tactile_list:
                print(f"x:{finger_tactile.x}, y:{finger_tactile.y}, z:{finger_tactile.z}")
            time.sleep(1 / hz)

    def undistort_image(self) -> None:
        """
        校正图像
        步骤：
        (1) 激活灵巧手
        (2) 查询灵巧手连接状态
        (3) 获取相机内参矩阵和畸变系数(分辨率为640x480)
        (4) 获取相机外参矩阵
        (5) 设置相机分辨率和帧率(分辨率为 640x480, 帧率为30fps)
        (6) 获取当前帧功能
        (7) 保存一帧图片
        (8) 图像校正（去畸变）
        (9) 保存校正后的图像
        (10)断开灵巧手连接
        PS：example 中采用死循环来持续展示校正图像的效果，第(10)步没在 example 中体现
        如果要中断 example 的执行，请在命令行窗口按 ctrl + C
        :return: None
        """
        # 步骤:激活灵巧手
        handy_type = self.control.activeHandy(self.handy_port_num, self.camera_port_num)
        print(f"步骤: 激活灵巧手成功，灵巧手类型为{'左手' if handy_type == 1 else '右手'}")
        # 步骤:查询灵巧手连接状态
        is_online = self.control.isConnectHandy()
        if not is_online:
            print("灵巧手未连接")
            return
        # 步骤：获取相机内参矩阵和畸变系数(分辨率为 640x480 )
        if self.control.setCameraConfig(640, 480, 30):
            print("设置相机分辨率和帧率成功")
            frame = self.control.getFrame()  # get the frame from the camera
            self.control.saveImage("ori_frame_640.png", frame)
        else:
            print("设置相机分辨率和帧率失败")
        # 步骤：获取相机内参矩阵和畸变系数(分辨率为 640x480)
        intrinsic_640_480, coeffs_640_480 = self.control.getIntrinsicMatrixAndDistCoeffs(640, 480)
        # 步骤：获取相机外参矩阵
        hand_eye_matrix = self.control.getHandEyeMatrix()
        print("相机外参矩阵: ", hand_eye_matrix)
        # 步骤：图像校正（去畸变）
        undistort_frame_640_480 = self.control.undistortImage(frame, intrinsic_640_480, coeffs_640_480)
        self.control.saveImage("undistort_frame_640_480.png", undistort_frame_640_480)  # save the image
        while True:
            ori_frame = self.control.getFrame()
            cv2.imshow("ori_frame", ori_frame)
            undistort_frame = self.control.undistortImage(ori_frame, intrinsic_640_480, coeffs_640_480)
            cv2.imshow("undistort_frame", undistort_frame)
            key = cv2.waitKey(1)
            if key == ord('q'):
                break
        # 步骤:断开灵巧手连接
        is_success = self.control.disconnectHandy()
        print(f"步骤:断开灵巧手连接{'成功' if is_success else '失败'}")
