import argparse

from dexh13_example import DexH13Example

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="DexH13 Python Example",
        epilog="示例: python3 main.py --example_num 1"
    )
    parser.add_argument("--example_num", "-num", required=True, help="example 序号(1:版本信息 2:角度控制 3:弧度控制 4:手势执行 5:速度模式 6:图像校正 7:传感器数据)")
    args = parser.parse_args()
    dexh13_example = DexH13Example(handy_port_num="/dev/ttyUSB0", camera_port_num="/dev/video4")
    example_num = int(args.example_num)
    if example_num == 1: 
        dexh13_example.get_version()
    if example_num == 2:
        dexh13_example.set_joint_angle()
    if example_num == 3:
        dexh13_example.set_joint_radian()
    if example_num == 4:
        dexh13_example.run_gestures()
    if example_num == 5:
        dexh13_example.run_speed_control_mode()
    if example_num == 6:
        dexh13_example.undistort_image()
    if example_num == 7:
        dexh13_example.get_finger_tactile()
