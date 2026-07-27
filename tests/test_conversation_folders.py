"""Tests for sorting chat media into per-conversation output folders."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import chat_media_utils as cmu


def test_plain_titles_pass_through():
    assert cmu.sanitize_folder_name("Beach Trip 2024") == "Beach Trip 2024"
    assert cmu.sanitize_folder_name("jane_doe99") == "jane_doe99"


def test_illegal_characters_replaced():
    assert cmu.sanitize_folder_name('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert cmu.sanitize_folder_name("line\nbreak") == "line_break"


def test_separator_runs_collapse():
    assert cmu.sanitize_folder_name('Ski/Trip: "24"') == "Ski_Trip_24"
    # A space next to a replaced character is absorbed into the separator.
    assert cmu.sanitize_folder_name("me & you <3") == "me & you_3"
    # A single underscore in a username must survive untouched.
    assert cmu.sanitize_folder_name("jane_doe") == "jane_doe"


def test_trailing_dots_and_spaces_stripped():
    # Windows silently drops these, so the folder we create would not be the
    # folder we then write into.
    assert cmu.sanitize_folder_name("Squad. ") == "Squad"
    assert cmu.sanitize_folder_name("  spaced  out  ") == "spaced out"


def test_empty_and_unusable_titles_fall_back():
    assert cmu.sanitize_folder_name("") == cmu.UNSORTED_FOLDER
    assert cmu.sanitize_folder_name(None) == cmu.UNSORTED_FOLDER
    assert cmu.sanitize_folder_name("...") == cmu.UNSORTED_FOLDER
    assert cmu.sanitize_folder_name("", fallback="Other") == "Other"


def test_reserved_windows_names_get_suffix():
    assert cmu.sanitize_folder_name("CON") == "CON_"
    assert cmu.sanitize_folder_name("nul") == "nul_"
    assert cmu.sanitize_folder_name("COM1") == "COM1_"
    assert cmu.sanitize_folder_name("console") == "console"


def test_long_titles_truncated_without_trailing_junk():
    name = cmu.sanitize_folder_name("A" * 200)
    assert len(name) == 60
    name = cmu.sanitize_folder_name("B" * 59 + ". tail")
    assert not name.endswith((".", " "))


def test_conversation_folder_uses_group_title():
    record = {"match": {"_conversation": "Ski Trip 🎿", "From": "bob"}}
    assert cmu.conversation_folder(record) == "Ski Trip 🎿"


def test_conversation_folder_unmatched_goes_to_fallback():
    assert cmu.conversation_folder({}) == cmu.UNSORTED_FOLDER
    assert cmu.conversation_folder({"match": None}) == cmu.UNSORTED_FOLDER


def test_build_chat_index_sets_conversation(tmp_path):
    """1:1 threads have no title, so the conversation key names the folder."""
    json_dir = tmp_path / "json"
    json_dir.mkdir()
    (json_dir / "chat_history.json").write_text(
        '{"jane_doe": [{"Media IDs": "b~abc", "From": "jane_doe",'
        ' "Created": "2024-03-01 10:00:00 UTC"}],'
        ' "conv-uuid-1": [{"Media IDs": "b~def", "From": "bob",'
        ' "Conversation Title": "Ski Trip", "Created": "2024-03-01 11:00:00 UTC"}]}',
        encoding="utf-8")

    index = cmu.build_chat_index(str(json_dir))
    assert cmu.conversation_folder(
        {"match": index["id_to_msg"]["b~abc"]}) == "jane_doe"
    assert cmu.conversation_folder(
        {"match": index["id_to_msg"]["b~def"]}) == "Ski Trip"


def test_sanitized_folder_is_creatable(tmp_path):
    for raw in ('Trip: "Big/Little" <2024>', "CON", "dots...", "   "):
        folder = tmp_path / cmu.sanitize_folder_name(raw)
        folder.mkdir(exist_ok=True)
        (folder / "x.jpg").write_bytes(b"x")
        assert folder.is_dir(), raw
        # The folder the OS actually created must match the name we chose.
        assert folder.name in os.listdir(tmp_path), raw


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
