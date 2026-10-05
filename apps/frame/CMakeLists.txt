cmake_minimum_required(VERSION 3.16)
project(frame VERSION 0.1 LANGUAGES CXX)

find_package(Qt6 REQUIRED COMPONENTS Quick)
qt_standard_project_setup(REQUIRES 6.5)

qt_add_executable(frame main.cpp)
qt_add_qml_module(frame URI app VERSION 1.0 QML_FILES Main.qml)
target_link_libraries(frame PRIVATE Qt6::Quick)
