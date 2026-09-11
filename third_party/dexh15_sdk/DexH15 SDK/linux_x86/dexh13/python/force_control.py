import math
import time

from pxdex.dh13 import DexH13Control, DexH13ForceControl, ControlMode, HandType, ForceCtlParam

if __name__ == "__main__":
    """
    力控模式
    步骤：
        (1) 连接灵巧手
        (2) 初始化电机位置
        (3) 设置控制模式-位置模式
        (4) 使能电机
        (5) 设置力控参数
        (6) 获取当前弧度
        (7) 获取当前传感器合力数据
        (8) 计算下次运动的目标弧度
        (9) 重复步骤(6)、步骤(7)和步骤(8)，直至握住物体 
        (10) 断开灵巧手连接
    """
    control, force_control = DexH13Control(), DexH13ForceControl()
    # 连接灵巧手
    handy_type = control.activeHandy('/dev/ttyUSB0', 'none')
    print(f"激活灵巧手成功，灵巧手类型为{'左手' if handy_type == 1 else '右手'}")
    time.sleep(1)
    # 初始化电机位置(如果刚上电时, 已执行初始化电机位置, 可跳过)
    is_success = control.initMotorPosition()
    print(f"初始化电机位置{'成功' if is_success == 1 else '失败'}")
    time.sleep(1)
    # 设置控制模式-位置模式
    is_success = control.setMotorControlMode(ControlMode.POSITION_CONTROL_MODE)
    print(f"设置控制模式(位置模式){'成功' if is_success else '失败'}")
    time.sleep(1)
    # 使能手指电机
    is_success = control.enableMotor()
    print(f"使能电机{'成功' if is_success else '失败'}")
    time.sleep(1)
    # 设置起始位置
    control.setJointPositionsRadian([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, math.pi / 180 * 90, 0, 0])
    time.sleep(1)
    # 设置力控参数
    force_ctl_param = ForceCtlParam()
    force_ctl_param.impedance_factor = []
    force_ctl_param.reference_force_factor = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0]
    force_ctl_param.enable_flag = [True, True, True, True, True, True, True, True]
    force_control.setForceCtlParam(HandType(handy_type), force_ctl_param)
    # 获取当前弧度
    cur_serial_pos_vec = control.getJointPositionsRadian()
    time.sleep(0.005)
    # 开始进行力控操作
    start_time = time.time()
    while time.time() - start_time < 5:
        # 当前传感器合力数据
        force_points = control.getFingerTactile()
        # 计算下次运动的目标弧度
        result = force_control.computeSerialTargetCommand(HandType(handy_type), cur_serial_pos_vec, force_points)
        # 设置下次运动的目标弧度
        control.setJointPositionsRadian(result[1])
        # 获取当前弧度
        cur_serial_pos_vec = control.getJointPositionsRadian()

    # 断开灵巧手连接
    is_success = control.disconnectHandy()
    print(f"断开灵巧手连接{'成功' if is_success else '失败'}")
