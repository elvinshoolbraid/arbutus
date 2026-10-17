add_arbutus_engine(plate SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/plate")
set(_in "${ARB_AUDIO_ROOT}/tests/golden/fx_input.npy")

# Frozen from this box (there is no Python original: the emulator never had a
# plate), with every path moving: a long DECAY, a big SIZE, damping, a
# predelay, the modulated tank. README § Tests.
add_test(NAME plate_golden COMMAND arbutus-run "${_box}"
         --in "${_in}" --len 48000 --sr 48000 --frames 512
         --set decay=8 --set mix=0.5 --set size=0.8 --set damp=0.4 --set predelay=30
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/plate.npy" --tol -100)

add_test(NAME ragged_plate
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --in "${_in}" --len 48000 --sr 48000
                 --set decay=8 --set mix=0.5 --set size=0.8 --set damp=0.4 --set predelay=30)

# MIX 0 is the input, bit for bit, with the tank at its longest.
add_test(NAME plate_bypass COMMAND arbutus-run "${_box}"
         --in "${_in}" --sr 48000 --frames 512 --set mix=0 --set decay=30 --set size=1 --set damp=0
         --golden "${_in}" --tol -999)

add_test(NAME rt_guard_plate COMMAND arbutus-run "${_box}" --rt-guard
         --in "${_in}" --len 48000 --sr 48000 --set decay=30 --set size=1)
