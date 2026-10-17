add_arbutus_engine(pattern SOURCES src/engine.cpp)
# The pattern asset is JSON: the vendored single header, compiled in (as endless).
target_link_libraries(pattern PRIVATE arb_json)

set(_box "${ARB_BOX_ROOT}/arbutus/pattern")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")
set(_hold_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes_hold.txt")
set(_asset "${CMAKE_CURRENT_SOURCE_DIR}/tests/pattern.json")

# A sequencer's golden is its event list, not audio (§9.4 by extension), as
# `endless`: --events-out dumps what it emitted, rebased onto the run's timeline.
function(arb_pattern_events case notes)
  set(_out "${CMAKE_CURRENT_BINARY_DIR}/${case}.json")
  add_test(NAME pattern_${case}_events COMMAND arbutus-run "${_box}"
           --notes "${notes}" --len 96000 --sr 48000 --frames 512 --tempo 120
           --seed 5 --events-out "${_out}" ${ARGN})
  add_test(NAME pattern_${case}_golden COMMAND ${CMAKE_COMMAND} -E compare_files
           "${_out}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/${case}.json")
  set_tests_properties(pattern_${case}_events PROPERTIES FIXTURES_SETUP pattern_${case}_dump)
  set_tests_properties(pattern_${case}_golden PROPERTIES FIXTURES_REQUIRED pattern_${case}_dump)
endfunction()

# Entered through the soft-keys, played back on a key: absolute pitch, so
# holding G plays the notes that were entered and not a transposition.
arb_pattern_events(entry "${_notes}")
# The same four steps on a STEPS 8 grid, an octave down, with HOLD: four notes
# then four rests, running with no key down.
arb_pattern_events(grid "${_hold_notes}" --asset pattern=${_asset}
                   --set steps=8 --set oct=-1 --set hold=1)
# The asset (chord, tie, note, rest) under PNG with swing.
arb_pattern_events(ping "${_hold_notes}" --asset pattern=${_asset}
                   --set dir=2 --set swing=66 --set hold=1 --set gate=50)

add_test(NAME rt_guard_pattern COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_hold_notes}" --asset pattern=${_asset}
         --len 96000 --sr 48000 --tempo 120
         --set dir=3 --set steps=13 --set gate=45 --set hold=1)

add_executable(test_pattern_format tests/test_format.cpp)
target_link_libraries(test_pattern_format PRIVATE arb_sdk arb_hostlib)
add_dependencies(test_pattern_format pattern_box)
add_test(NAME pattern_format COMMAND test_pattern_format "${_box}" "${_asset}")

# Rule 8 for what a sequencer makes: its events, at every block size, with a
# tempo that puts no step on a round frame (#631, test 10).
add_test(NAME ragged_events_pattern
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged_events.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged_events"
                 --notes "${_notes}" --len 96000 --sr 48000 --tempo 97
                 --set swing=63 --set gate=37)

# #864: STEPS' AUTO readout formatted while process() moves the length on
# another thread. The engine's source is compiled into the test so
# ThreadSanitizer sees both sides; without TSAN it still checks the values.
include(CheckCXXSourceCompiles)
set(CMAKE_REQUIRED_FLAGS "-fsanitize=thread")
set(CMAKE_REQUIRED_LINK_OPTIONS "-fsanitize=thread")
check_cxx_source_compiles("int main() { return 0; }" ARB_HAVE_TSAN)
unset(CMAKE_REQUIRED_FLAGS)
unset(CMAKE_REQUIRED_LINK_OPTIONS)
find_package(Threads REQUIRED)
add_executable(test_pattern_format_threads tests/test_format_threads.cpp src/engine.cpp)
target_link_libraries(test_pattern_format_threads PRIVATE arb_sdk arb_json Threads::Threads)
if(ARB_HAVE_TSAN)
    target_compile_options(test_pattern_format_threads PRIVATE -fsanitize=thread)
    target_link_options(test_pattern_format_threads PRIVATE -fsanitize=thread)
endif()
add_test(NAME pattern_format_threads COMMAND test_pattern_format_threads)
