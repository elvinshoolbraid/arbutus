add_arbutus_engine(reverb SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/reverb")
set(_in "${ARB_AUDIO_ROOT}/tests/golden/fx_input.npy")

# The cross-language proof for this box: tests/golden/reverb.npy was captured
# from emulator/engines/effects.py SchroederReverb at SIZE 0.5 / MIX 0.25 by
# tests/tools/make_fx_goldens.py.
add_test(NAME reverb_golden COMMAND arbutus-run "${_box}"
         --in "${_in}" --sr 48000 --frames 256
         --set size=0.5 --set mix=0.25
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/reverb.npy" --tol -100)

# ...and the C++ is block-size independent where the Python was not: the same
# numbers at 512, 128 and 97. See tests/tools/make_fx_goldens.py.
add_test(NAME ragged_reverb
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --in "${_in}" --sr 48000 --set size=0.5 --set mix=0.25)

add_test(NAME rt_guard_reverb COMMAND arbutus-run "${_box}" --rt-guard
         --in "${_in}" --sr 48000)
