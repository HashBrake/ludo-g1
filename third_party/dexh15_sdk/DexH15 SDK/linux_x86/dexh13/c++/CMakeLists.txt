# 设置CMake的最低版本要求
cmake_minimum_required(VERSION 3.10) 

project(DexH13_SDK_Example)

# 设置C++标准
set(CMAKE_CXX_STANDARD 17)

# 设置C++标准必须使用
set(CMAKE_CXX_STANDARD_REQUIRED True)

find_package(DexHandSDK REQUIRED)
if(DexHandSDK_FOUND)
    include_directories(${DexHandSDK_INCLUDE_DIRS})
endif()

find_package(OpenCV REQUIRED)
if(OpenCV_FOUND)
    include_directories(${OpenCV_INCLUDE_DIRS})
endif()  

# 添加头文件目录
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/include)
aux_source_directory(${CMAKE_CURRENT_SOURCE_DIR}/include DEXH13_EXAMPLE_INC)

add_executable(main ${DEXH13_EXAMPLE_INC} ${CMAKE_CURRENT_SOURCE_DIR}/src/main.cpp ${CMAKE_CURRENT_SOURCE_DIR}/src/dexh13_example.cpp)
target_link_libraries(main PRIVATE 
    ${OpenCV_LIBS} 
    ${DEXHANDSDK_ALL_LIBS}
)

add_executable(position_control ${CMAKE_CURRENT_SOURCE_DIR}/src/position_control.cpp)
target_link_libraries(position_control PRIVATE 
    ${DEXHANDSDK_ALL_LIBS}
)

add_executable(finger_tactile ${CMAKE_CURRENT_SOURCE_DIR}/src/finger_tactile.cpp)
target_link_libraries(finger_tactile PRIVATE 
    ${DEXHANDSDK_ALL_LIBS}
)

add_executable(undistort_image ${CMAKE_CURRENT_SOURCE_DIR}/src/undistort_image.cpp)
target_link_libraries(undistort_image PRIVATE 
    ${OpenCV_LIBS} 
    ${DEXHANDSDK_ALL_LIBS}
)

add_executable(force_control ${CMAKE_CURRENT_SOURCE_DIR}/src/force_control.cpp)
target_link_libraries(force_control PRIVATE 
    ${DEXHANDSDK_ALL_LIBS}
)

add_executable(speed_force_control ${CMAKE_CURRENT_SOURCE_DIR}/src/speed_force_control.cpp)
target_link_libraries(speed_force_control PRIVATE 
    ${DEXHANDSDK_ALL_LIBS}
)

add_executable(upgrade_mainboard ${CMAKE_CURRENT_SOURCE_DIR}/src/upgrade_mainboard.cpp)
target_link_libraries(upgrade_mainboard PRIVATE 
    ${DEXHANDSDK_ALL_LIBS}
)

add_executable(upgrade_driveboard ${CMAKE_CURRENT_SOURCE_DIR}/src/upgrade_driveboard.cpp)
target_link_libraries(upgrade_driveboard PRIVATE 
    ${DEXHANDSDK_ALL_LIBS}
)