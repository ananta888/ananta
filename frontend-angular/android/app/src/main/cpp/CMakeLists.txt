cmake_minimum_required(VERSION 3.22.1)
project(ananta_llama_runtime LANGUAGES C CXX)

add_library(ananta_llama_runtime SHARED
    ananta_llama_runtime.cpp
)

find_library(log-lib log)

target_link_libraries(ananta_llama_runtime
    ${log-lib}
)
