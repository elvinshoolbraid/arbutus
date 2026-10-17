add_arbutus_engine(euclid SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/euclid")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")

# A sequencer's golden is its event list, not audio (§9.4 by extension), as
# `endless`: --events-out dumps what it emitted, rebased onto the run's timeline.
function(arb_euclid_events case)
  set(_out "${CMAKE_CURRENT_BINARY_DIR}/${case}.json")
  add_test(NAME euclid_${case}_events COMMAND arbutus-run "${_box}"
           --notes "${_notes}" --len 120000 --sr 48000 --frames 512 --tempo 120
           --events-out "${_out}" ${ARGN})
  add_test(NAME euclid_${case}_golden COMMAND ${CMAKE_COMMAND} -E compare_files
           "${_out}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/${case}.json")
  set_tests_properties(euclid_${case}_events PROPERTIES FIXTURES_SETUP euclid_${case}_dump)
  set_tests_properties(euclid_${case}_golden PROPERTIES FIXTURES_REQUIRED euclid_${case}_dump)
endfunction()

# E(4,16) at 1/16: the four-on-the-floor the defaults land on.
arb_euclid_events(four)
# E(5,8) -- the necklace people quote -- rotated by 2, with ACCENT on.
arb_euclid_events(five8 --set steps=8 --set pulses=5 --set rot=2 --set accent=70)
# E(3,8) at 1/8 with swing and HOLD: still running after the key is up.
arb_euclid_events(tresillo --set steps=8 --set pulses=3 --set div=1
                  --set swing=62 --set hold=1 --set gate=100)

add_test(NAME rt_guard_euclid COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --len 120000 --sr 48000 --tempo 120
         --set steps=31 --set pulses=13 --set rot=7 --set div=6 --set accent=40)

# Rule 8 for what a sequencer makes: its events, at every block size, with a
# tempo that puts no step on a round frame (#631, test 10).
add_test(NAME ragged_events_euclid
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged_events.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged_events"
                 --notes "${_notes}" --len 96000 --sr 48000 --tempo 97
                 --set swing=63 --set gate=37 --set steps=7 --set pulses=3 --set accent=50)
