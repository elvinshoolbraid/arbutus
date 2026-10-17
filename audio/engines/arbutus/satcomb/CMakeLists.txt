add_arbutus_engine(satcomb SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/satcomb")
set(_in "${ARB_AUDIO_ROOT}/tests/golden/fx_input.npy")

add_test(NAME satcomb_golden COMMAND arbutus-run "${_box}"
         --in "${_in}" --len 32768 --sr 48000 --frames 512
         --set tune=-12 --set feedback=0.95 --set damp=0.35 --set drive=4 --set mix=0.8
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/satcomb.npy" --tol -100)

add_test(NAME ragged_satcomb
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --in "${_in}" --sr 48000 --set tune=-12 --set feedback=0.95 --set drive=4)

add_test(NAME rt_guard_satcomb COMMAND arbutus-run "${_box}" --rt-guard
         --in "${_in}" --sr 48000)
