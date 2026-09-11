# 设置CMake的最低版本要求
cmake_minimum_required(VERSION 3.10) 

project(DexH5_SDK_Example)

# 设置C++标准
set(CMAKE_CXX_STANDARD 17)

# 设置C++标准必须使用
set(CMAKE_CXX_STANDARD_REQUIRED True)

find_package(DexHandSDK REQUIRED)
if(DexHandSDK_FOUND)
    include_directories(${DexHandSDK_INCLUDE_DIRS})
endif()

# 添加头文件目录
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/include)
aux_source_directory(${CMAKE_CURRENT_SOURCE_DIR}/include DEXH5_EXAMPLE_INC)
aux_source_directory(${CMAKE_CURRENT_SOURCE_DIR}/src DEXH5_EXAMPLE_SRCS)

# 测试生成器
add_executable(main ${DEXH5_EXAMPLE_INC} ${DEXH5_EXAMPLE_SRCS})

# 添加链接库 DexHandSDK
target_link_libraries(main PRIVATE 
    ${DEXHANDSDK_ALL_LIBS}
)
