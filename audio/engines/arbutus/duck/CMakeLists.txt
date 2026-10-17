add_arbutus_engine(duck SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/duck")
set(_in "${ARB_AUDIO_ROOT}/tests/golden/fx_input.npy")
set(_side "${ARB_AUDIO_ROOT}/tests/golden/fx_side.npy")

add_test(NAME duck_golden COMMAND arbutus-run "${_box}"
         --in "${_in}" --side "${_side}" --sr 48000 --frames 512
         --set threshold=-24 --set depth=0.9 --set attack=3 --set release=60
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/duck.npy" --tol -100)

add_test(NAME ragged_duck
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --in "${_in}" --side "${_side}" --sr 48000
                 --set threshold=-24 --set depth=0.9)

add_test(NAME rt_guard_duck COMMAND arbutus-run "${_box}" --rt-guard
         --in "${_in}" --side "${_side}" --sr 48000)
