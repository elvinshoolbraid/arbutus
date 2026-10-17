add_arbutus_engine(dbox SOURCES src/engine.cpp)
target_link_libraries(dbox PRIVATE arb_json)

set(_box "${ARB_BOX_ROOT}/arbutus/dbox")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")
set(_kit "${CMAKE_CURRENT_SOURCE_DIR}/tests/kit.json")

# The kit is params only -- no file on any pad. That shape is the one stage I1
# taught the host to load, so the golden is also the proof that it round-trips.
add_test(NAME dbox_golden COMMAND arbutus-run "${_box}"
         --notes "${_notes}" --len 48000 --sr 48000 --frames 512 --asset kit=${_kit}
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/dbox.npy" --tol -100)

add_test(NAME ragged_dbox
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --notes "${_notes}" --len 48000 --sr 48000 --asset kit=${_kit})

add_test(NAME rt_guard_dbox COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --len 48000 --sr 48000 --asset kit=${_kit})
