cmake_minimum_required(VERSION 3.16)

project(deepyield_stdf_parser VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# MSVC/clang-cl: suppress deprecation warnings for standard C functions
if(MSVC OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    add_compile_definitions(_CRT_SECURE_NO_WARNINGS)
endif()

# GCC < 9 needs -lstdc++fs for std::filesystem
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU" AND CMAKE_CXX_COMPILER_VERSION VERSION_LESS "9.0")
    set(CMAKE_CXX_STANDARD_LIBRARIES "${CMAKE_CXX_STANDARD_LIBRARIES} -lstdc++fs")
endif()

# --- Arrow / Parquet discovery (bundled with pyarrow) ---
if(NOT Arrow_ROOT)
    if(DEFINED ENV{CONDA_PREFIX})
        set(Arrow_ROOT "$ENV{CONDA_PREFIX}/Lib/site-packages/pyarrow")
    else()
        execute_process(
            COMMAND python -c "import pyarrow; print(pyarrow.__file__.rsplit('\\\\',1)[0])" 2>/dev/null
            OUTPUT_VARIABLE _pyarrow_dir
            OUTPUT_STRIP_TRAILING_WHITESPACE
            ERROR_QUIET
        )
        if(_pyarrow_dir)
            set(Arrow_ROOT "${_pyarrow_dir}")
        endif()
    endif()
endif()

message(STATUS "Arrow_ROOT = ${Arrow_ROOT}")

set(ARROW_INCLUDE_DIR "${Arrow_ROOT}/include")
set(ARROW_LIB_DIR "${Arrow_ROOT}")
get_filename_component(_PYARROW_PARENT "${Arrow_ROOT}" DIRECTORY)
set(PYARROW_DLL_DIR "${_PYARROW_PARENT}/pyarrow.libs")

# --- BZip2 discovery (prefer CONDA_PREFIX, fallback to system) ---
if(DEFINED ENV{CONDA_PREFIX})
    set(BZIP2_INCLUDE_DIR "$ENV{CONDA_PREFIX}/Library/include")
    set(BZIP2_LIBRARIES "$ENV{CONDA_PREFIX}/Library/lib/libbz2.lib")
    set(BZIP2_DLL_DIR "$ENV{CONDA_PREFIX}/Library/bin")
else()
    find_package(BZip2 REQUIRED)
endif()
message(STATUS "BZIP2_INCLUDE_DIR = ${BZIP2_INCLUDE_DIR}")
message(STATUS "BZIP2_LIBRARIES = ${BZIP2_LIBRARIES}")

# --- deepyield_stdf library ---
add_library(deepyield_stdf
    src/binary_reader.cpp
    src/record_dispatcher.cpp
    src/bz2_streambuf.cpp
)
target_include_directories(deepyield_stdf PUBLIC include)
target_include_directories(deepyield_stdf PRIVATE ${BZIP2_INCLUDE_DIR})
target_link_libraries(deepyield_stdf PUBLIC ${BZIP2_LIBRARIES})

# --- direct ATVI writer (plan WP3) ---
add_library(deepyield_atvi
    src/atvi_writer.cpp
    src/atvi_compact.cpp
    src/atvi_classifier.cpp
    src/atvi_arrow.cpp
)
target_include_directories(deepyield_atvi PUBLIC include ${ARROW_INCLUDE_DIR})
target_link_directories(deepyield_atvi PUBLIC ${ARROW_LIB_DIR})
target_link_libraries(deepyield_atvi PUBLIC deepyield_stdf arrow parquet)
if(MINGW)
    target_link_options(deepyield_stdf INTERFACE -static -static-libstdc++ -static-libgcc)
endif()

# --- stdf2parquet executable ---
add_executable(stdf2parquet src/main.cpp src/parquet_writer.cpp src/progress.cpp src/perf_counters.cpp src/logger.cpp src/column_registry.cpp)
target_sources(stdf2parquet PRIVATE src/release_parser.cpp src/release_metadata.cpp
    src/release_arrow.cpp src/release_input.cpp)
if(MSVC)
    target_compile_options(stdf2parquet PRIVATE /utf-8)
endif()
if(WIN32)
    target_link_libraries(stdf2parquet PRIVATE shell32)
endif()
target_include_directories(stdf2parquet PRIVATE ${ARROW_INCLUDE_DIR})
target_link_directories(stdf2parquet PRIVATE ${ARROW_LIB_DIR})
target_link_libraries(stdf2parquet PRIVATE deepyield_stdf deepyield_atvi arrow parquet)

# Copy Arrow/Parquet DLLs next to the executable so they are found at runtime.
# PyPI/conda pyarrow stores core DLLs under pyarrow/ and private runtime
# dependencies under the sibling pyarrow.libs/ directory.
file(GLOB PYARROW_CORE_DLLS "${ARROW_LIB_DIR}/*.dll")
file(GLOB PYARROW_RUNTIME_DLLS "${PYARROW_DLL_DIR}/*.dll")
foreach(_dll ${PYARROW_CORE_DLLS} ${PYARROW_RUNTIME_DLLS})
    add_custom_command(TARGET stdf2parquet POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${_dll}"
            "$<TARGET_FILE_DIR:stdf2parquet>"
    )
endforeach()

# Copy bzip2 DLL next to the executable
if(DEFINED BZIP2_DLL_DIR)
    file(GLOB BZIP2_DLLS "${BZIP2_DLL_DIR}/libbz2.dll")
    foreach(_dll ${BZIP2_DLLS})
        add_custom_command(TARGET stdf2parquet POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${_dll}"
                "$<TARGET_FILE_DIR:stdf2parquet>"
        )
    endforeach()
elseif(DEFINED ENV{CONDA_PREFIX})
    file(GLOB BZIP2_DLLS "$ENV{CONDA_PREFIX}/Library/bin/libbz2.dll")
    foreach(_dll ${BZIP2_DLLS})
        add_custom_command(TARGET stdf2parquet POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${_dll}"
                "$<TARGET_FILE_DIR:stdf2parquet>"
        )
    endforeach()
endif()

enable_testing()

add_executable(test_record_header tests/test_record_header.cpp)
target_link_libraries(test_record_header PRIVATE deepyield_stdf)
add_test(NAME test_record_header COMMAND test_record_header)

add_executable(test_dispatcher tests/test_dispatcher.cpp)
target_link_libraries(test_dispatcher PRIVATE deepyield_stdf)
add_test(NAME test_dispatcher COMMAND test_dispatcher)

add_executable(test_bz2_stream tests/test_bz2_stream.cpp)
target_link_libraries(test_bz2_stream PRIVATE deepyield_stdf)
add_test(NAME test_bz2_stream COMMAND test_bz2_stream)

add_executable(test_digit_prefix tests/test_digit_prefix.cpp)
add_test(NAME test_digit_prefix COMMAND test_digit_prefix)

add_executable(test_sha256 tests/test_sha256.cpp)
target_link_libraries(test_sha256 PRIVATE deepyield_stdf)
add_test(NAME test_sha256 COMMAND test_sha256)

add_executable(test_atvi_writer tests/test_atvi_writer.cpp)
target_link_libraries(test_atvi_writer PRIVATE deepyield_atvi)
add_test(NAME test_atvi_writer COMMAND test_atvi_writer)

add_executable(test_release_arrow tests/test_release_arrow.cpp src/release_parser.cpp
    src/release_metadata.cpp src/release_arrow.cpp)
target_link_libraries(test_release_arrow PRIVATE deepyield_atvi)
if(MSVC)
    target_compile_options(test_release_arrow PRIVATE /utf-8)
endif()
add_test(NAME test_release_arrow COMMAND test_release_arrow)
