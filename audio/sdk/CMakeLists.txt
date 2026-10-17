# The SDK: headers for engines, the manifest generator, add_arbutus_engine().

# What an engine compiles against. Header-only by design — a box links nothing
# from us, so an SDK bump cannot break a box that was already built.
add_library(arb_sdk INTERFACE)
target_include_directories(arb_sdk INTERFACE
  "${CMAKE_CURRENT_SOURCE_DIR}/include"
  "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_features(arb_sdk INTERFACE cxx_std_20)

# Host-side JSON. Engines never see it.
add_library(arb_json INTERFACE)
target_include_directories(arb_json SYSTEM INTERFACE "${ARB_AUDIO_ROOT}/third_party")

# ---- target arch -----------------------------------------------------------
# §9.5: native builds on each target for v1. The arch key is <os>-<cpu> and the
# two that exist are darwin-arm64 (this Mac) and linux-aarch64 (the CM4).
if(APPLE)
  set(ARB_ARCH "darwin-${CMAKE_SYSTEM_PROCESSOR}" CACHE INTERNAL "")
  set(ARB_MODULE_SUFFIX ".dylib" CACHE INTERNAL "")
elseif(UNIX)
  set(ARB_ARCH "linux-${CMAKE_SYSTEM_PROCESSOR}" CACHE INTERNAL "")
  set(ARB_MODULE_SUFFIX ".so" CACHE INTERNAL "")
else()
  message(FATAL_ERROR "no box arch name for this platform")
endif()
message(STATUS "arbutus: boxes build for ${ARB_ARCH}, boxes in ${ARB_BOX_ROOT}")

add_executable(arb-manifest tools/arb_manifest.cpp)
target_link_libraries(arb-manifest PRIVATE arb_sdk arb_json ${CMAKE_DL_LIBS})
# A cross build (#568, cmake/aarch64-trixie.cmake) runs this target, and only
# this one, through an emulator: it dlopen()s each box to write engine.json.
if(CMAKE_CROSSCOMPILING AND ARB_CROSS_EMULATOR)
  set_target_properties(arb-manifest PROPERTIES
    CROSSCOMPILING_EMULATOR "${ARB_CROSS_EMULATOR}")
endif()

# ---- add_arbutus_engine(<name> SOURCES ... [MANIFEST f] [AUTHOR a] [ROOT d])
# Builds the §3.9 box folder:
#     <build>/boxes/<author>/<name>/engine.json
#     <build>/boxes/<author>/<name>/<arch>/<name>.dylib|.so
#     <build>/boxes/<author>/<name>/assets/        (if the source dir has one)
# engine.json is generated from arb_describe() by arb-manifest, so the snapshot
# the loader validates cannot drift from the binary it describes.
# ROOT replaces <build>/boxes for a box that must NOT be offered to the
# instrument -- a test fixture the host would otherwise index and the UI would
# then list (#52's allocating box is the only one).
function(add_arbutus_engine name)
  cmake_parse_arguments(ARB "" "MANIFEST;AUTHOR;ROOT" "SOURCES" ${ARGN})
  if(NOT ARB_SOURCES)
    message(FATAL_ERROR "add_arbutus_engine(${name}): SOURCES is required")
  endif()
  if(NOT ARB_MANIFEST)
    set(ARB_MANIFEST "${CMAKE_CURRENT_SOURCE_DIR}/engine.json")
  endif()
  if(NOT EXISTS "${ARB_MANIFEST}")
    message(FATAL_ERROR "add_arbutus_engine(${name}): no manifest at ${ARB_MANIFEST}")
  endif()
  if(NOT ARB_AUTHOR)
    file(READ "${ARB_MANIFEST}" _arb_src_json)
    string(JSON ARB_AUTHOR ERROR_VARIABLE _arb_err GET "${_arb_src_json}" author)
    if(_arb_err)
      message(FATAL_ERROR "add_arbutus_engine(${name}): ${ARB_MANIFEST} has no \"author\"")
    endif()
  endif()

  if(NOT ARB_ROOT)
    set(ARB_ROOT "${ARB_BOX_ROOT}")
  endif()
  set(_box "${ARB_ROOT}/${ARB_AUTHOR}/${name}")

  add_library(${name} MODULE ${ARB_SOURCES})
  target_link_libraries(${name} PRIVATE arb_sdk)
  set_target_properties(${name} PROPERTIES
    PREFIX ""
    OUTPUT_NAME "${name}"
    SUFFIX "${ARB_MODULE_SUFFIX}"
    LIBRARY_OUTPUT_DIRECTORY "${_box}/${ARB_ARCH}"
    C_VISIBILITY_PRESET hidden
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON)

  # An OUTPUT rule, not a POST_BUILD hook: the snapshot is then rebuilt when
  # arb-manifest itself changes, not only when the engine relinks.
  set(_assets_cmd "")
  if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/assets")
    set(_assets_cmd COMMAND ${CMAKE_COMMAND} -E copy_directory
                    "${CMAKE_CURRENT_SOURCE_DIR}/assets" "${_box}/assets")
  endif()
  add_custom_command(OUTPUT "${_box}/engine.json"
    COMMAND arb-manifest
            "$<TARGET_FILE:${name}>" "${ARB_MANIFEST}" "${_box}/engine.json"
            "${ARB_ARCH}" "${ARB_ARCH}/${name}${ARB_MODULE_SUFFIX}" "${ARB_AUTHOR}/${name}"
    ${_assets_cmd}
    DEPENDS ${name} arb-manifest "${ARB_MANIFEST}"
    COMMENT "box ${ARB_AUTHOR}/${name}: engine.json from arb_describe()"
    VERBATIM)
  add_custom_target(${name}_box ALL DEPENDS "${_box}/engine.json")

  # Tests and scripts ask for a box by target name.
  set(ARB_BOX_${name} "${_box}" CACHE INTERNAL "box dir for ${name}")
endfunction()

add_subdirectory(template)
