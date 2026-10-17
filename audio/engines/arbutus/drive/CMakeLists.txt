add_arbutus_engine(drive SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/drive")
set(_in "${ARB_AUDIO_ROOT}/tests/golden/fx_input.npy")

# Frozen from this box (the emulator had no distortion but the MASTER strip's,
# which is a different circuit). FUZZ with BIAS and SAG up exercises the
# asymmetric path, the DC blocker and the moving gain; the HP and the tilt are
# off centre so both are in the render. Checked with tests/tools/npystat.py;
# README § Tests.
add_test(NAME drive_golden COMMAND arbutus-run "${_box}"
         --in "${_in}" --sr 48000 --frames 512
         --set mode=3 --set drive=0.65 --set tone=0.68 --set mix=0.85
         --set bias=0.4 --set hp=90 --set sag=0.5 --set out=-4
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/drive.npy" --tol -100)

add_test(NAME ragged_drive
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --in "${_in}" --sr 48000
                 --set mode=3 --set drive=0.65 --set tone=0.68 --set mix=0.85
                 --set bias=0.4 --set hp=90 --set sag=0.5 --set out=-4)

add_test(NAME rt_guard_drive COMMAND arbutus-run "${_box}" --rt-guard
         --in "${_in}" --sr 48000
         --set mode=2 --set drive=1 --set bias=1 --set sag=1 --set hp=400)
