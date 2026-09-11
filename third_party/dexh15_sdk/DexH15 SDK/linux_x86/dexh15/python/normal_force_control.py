import sys
import time
from pxdex.dh15 import DexH15Control, ControlMode, ForceCtlParam, Dex15MotorPosition

# 手动连接灵巧手使用法向力力控参考示例
if __name__ == "__main__":
    """
    步骤示例：
        1. 创建串口控制实例
        2. 打开 Modbus 串口设备
        3. 初始化从站设备信息
        4. 初始化电机位置（上电周期内执行过则可跳过此操作）
        5. 设置位置模式
        6. 使能电机
        7. 查询使能状态
        8. 设置力控参数
        9. 持续进行力控目标位置计算并进行计算（用户可按需修改条件）
        10. 回到初始零位
        11. 下使能电机
        12. 关闭 Modbus 串口
    """
    # 1. 创建串口控制实例
    hand_control = DexH15Control()

    # Modbus 串口号
    hand_port_num = "/dev/ttyUSB0"
    # 波特率
    baud_rate = 4000000
    # Modbus 从站地址
    slave_address = 0x78

    # 2. 打开 Modbus 串口
    if not hand_control.openModbusDevice(hand_port_num, baud_rate):
        print("请检查是否插入 Modbus 串口设备")
        sys.exit(1)
    print("打开 Modbus 串口成功")

    # 3. 初始化 Modbus 从站
    if hand_control.initModbusDevice(slave_address) != 1:
        print("请检查灵巧手是否接入 Modbus 串口设备")
        hand_control.disconnectModbus()
        sys.exit(1)
    print("初始化 Modbus 从站成功")

    # 4. 初始化电机位置(如果刚上电时已调用，可跳过)
    if hand_control.initMotorPosition(slave_address) != 1:
        print("初始化电机位置失败")
        hand_control.disconnectModbus()
        sys.exit(1)
    print("初始化电机位置成功")

    # 6. 设置位置模式
    if not hand_control.setMotorControlMode(slave_address, ControlMode.POSITION_CONTROL_MODE):
        print("设置位置模式失败，无法进行位置模式控制")
        hand_control.disconnectModbus()
        sys.exit(1)
    print("设置位置模式成功")

    # 7. 使能电机
    if not hand_control.enableMotor(slave_address):
        print("使能电机失败，无法进行位置模式控制")
        hand_control.disconnectModbus()
        sys.exit(1)
    print("使能电机成功")

    # 8. 查询使能状态
    if not hand_control.isMotorEnabled(slave_address):
        print("电机状态为未使能，无法进行位置模式控制")
        hand_control.disconnectModbus()
        sys.exit(1)
    print("电机状态为已使能")

    # 9. 法向力力控参数
    force_ctl_param: ForceCtlParam = ForceCtlParam()
    # 初始位姿
    original_angle = [0, 0, 0, 0, 0, 0, 0, 0, 300, 0, 0]

    # 设置法向力力控参数
    # 根据实际 Python 绑定接口调整参数传递方式
    if hand_control.setCommonForceCtlParam(slave_address, original_angle, force_ctl_param) != 1:
        print("设置法向力力控参数失败")
        hand_control.disconnectModbus()
        sys.exit(1)
    print("设置法向力力控参数成功")
    time.sleep(2)

    # 10. 执行法向力力位混合模式控制（30秒）
    print("\n开始执行法向力力位混合模式控制（30秒）")
    print("-" * 50)
    
    start_time = time.time()
    duration = 30  # 30秒
    
    try:
        while time.time() - start_time < duration:
            # 计算下次运动的目标角度
            ret, command = hand_control.computeCommonForceTargetCommand(slave_address)
            if not ret:
                print("计算下次运动的目标角度失败")
                break
            
            print(f"计算下次运动的目标角度成功: {command}")
            
            try:
                # 设置下次运动目标角度
                if hand_control.setJointPositionsAngle(slave_address, command) != 1:
                    print("设置下次运动目标角度失败")
                    break
            except RuntimeError as e:
                if "Interrupted" not in str(e):
                    raise  # 如果不是中断错误，重新抛出
            print("设置下次运动目标角度成功")
            
            time.sleep(0.005)  # 5毫秒 = 0.005秒
            
    except KeyboardInterrupt:
        print("\n用户中断程序")
    
    finally:
        # 11. 电机回到零位
        zero_positions = Dex15MotorPosition()
        if not hand_control.setMotorTargetPosition(slave_address, zero_positions):
            print("电机位置回零失败")
        else:
            print("电机位置回零成功")
        time.sleep(0.5)

        # 12. 电机下使能
        is_success = hand_control.disableMotor(slave_address)
        print(f"电机下使能：{'成功' if is_success else '失败'}")
    
        # 13. 关闭 Modbus 串口
        result = hand_control.disconnectModbus()
        print(f"断开连接：{' 成功' if result else ' 失败'}")