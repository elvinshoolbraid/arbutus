add_arbutus_engine(endless SOURCES src/engine.cpp)
# The pattern asset is JSON: the vendored single header, compiled in (as stepmod).
target_link_libraries(endless PRIVATE arb_json)

set(_box "${ARB_BOX_ROOT}/arbutus/endless")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")
set(_events "${CMAKE_CURRENT_BINARY_DIR}/events.json")

# A sequencer's golden is its event list, not audio (§9.4 by extension): what
# this box produces is notes, and comparing rendered silence would prove
# nothing. --events-out dumps what it emitted, rebased onto the run's timeline.
add_test(NAME endless_events COMMAND arbutus-run "${_box}"
         --notes "${_notes}" --len 48000 --sr 48000 --frames 512 --tempo 120
         --events-out "${_events}")
add_test(NAME endless_golden COMMAND ${CMAKE_COMMAND} -E compare_files
         "${_events}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/events.json")
# compare_files can only run once the dump exists.
set_tests_properties(endless_events PROPERTIES FIXTURES_SETUP endless_dump)
set_tests_properties(endless_golden PROPERTIES FIXTURES_REQUIRED endless_dump)

add_test(NAME rt_guard_endless COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --len 48000 --sr 48000 --tempo 120)

# ---- stage H1: GATE, PATTERN and tied notes (engine-spec §15.4) ------------
# One dump + one compare each, the same fixture shape as the golden above.
set(_tie_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes_tie.txt")
set(_hold_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes_hold.txt")
set(_seq_asset "${CMAKE_CURRENT_SOURCE_DIR}/tests/pattern.json")

function(arb_endless_events case)
  set(_out "${CMAKE_CURRENT_BINARY_DIR}/${case}.json")
  add_test(NAME endless_${case}_events COMMAND arbutus-run "${_box}"
           --len 48000 --sr 48000 --frames 512 --tempo 120
           --events-out "${_out}" ${ARGN})
  add_test(NAME endless_${case}_golden COMMAND ${CMAKE_COMMAND} -E compare_files
           "${_out}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/${case}.json")
  set_tests_properties(endless_${case}_events PROPERTIES FIXTURES_SETUP ${case}_dump)
  set_tests_properties(endless_${case}_golden PROPERTIES FIXTURES_REQUIRED ${case}_dump)
endfunction()

# GATE 50 %: the same three steps, each note half a step long.
arb_endless_events(gate --notes "${_notes}" --set gate=50)
# PATTERN 4/4 over a 3-step phrase: the mask runs on its own 16-step counter,
# so the phrase precesses through it instead of locking to it.
arb_endless_events(mask --notes "${_notes}" --set pattern=1)
# A tie entered with the keys still down, and a plain rest after it.
arb_endless_events(tie --notes "${_tie_notes}")
# The pattern as a file (DDR-037) -- chord, tie, note, rest -- gated at 75 %.
arb_endless_events(asset --notes "${_hold_notes}" --asset pattern=${_seq_asset}
                   --set gate=75)

add_test(NAME rt_guard_endless_h1 COMMAND arbutus-run "${_box}" --rt-guard
         --asset pattern=${_seq_asset} --notes "${_hold_notes}"
         --len 48000 --sr 48000 --tempo 120 --set gate=60 --set pattern=4 --set rot=3)

# Rule 8 for what a sequencer makes: its events, at every block size, with a
# tempo that puts no step on a round frame (#631, test 10).
add_test(NAME ragged_events_endless
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged_events.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged_events"
                 --notes "${_notes}" --len 96000 --sr 48000 --tempo 97
                 --set swing=63 --set gate=37)
