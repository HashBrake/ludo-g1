import math
import time

from pxdex.dh13 import DexH13Control, ControlMode

if __name__ == "__main__":
    """
    位置模式
    步骤：
        (1) 连接灵巧手
        (2) 初始化电机位置
        (3) 设置控制模式-位置模式
        (4) 使能电机
        (5) 设置关节弧度
        (6) 断开灵巧手连接
    """
    control = DexH13Control()
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
    # 设置关节弧度
    radians = [math.pi / 180 * 10.0, math.pi / 180 * 50.0, math.pi / 180 * 50.0, math.pi / 180 * 50.0,
               math.pi / 180 * 10.0, math.pi / 180 * 50.0, math.pi / 180 * 50.0, math.pi / 180 * 50.0,
               math.pi / 180 * 10.0, math.pi / 180 * 50.0, math.pi / 180 * 50.0, math.pi / 180 * 50.0,
               math.pi / 180 * 10.0, math.pi / 180 * 50.0, math.pi / 180 * 50.0, math.pi / 180 * 50.0]
    is_success = control.setJointPositionsRadian(radians)
    print(f"设置关节弧度{'成功' if is_success else '失败'}")
    time.sleep(1)
    # 断开灵巧手连接
    is_success = control.disconnectHandy()
    print(f"断开灵巧手连接{'成功' if is_success else '失败'}")
