# TODO: move to tests, make real tests
from shutil import rmtree
from pathlib import Path
from toml_config import ConfigBuilder

TEST_DIR = Path("some_hopefully_nonexistent_dir")

def setup():
    if TEST_DIR.exists():
        rmtree(TEST_DIR)
    TEST_DIR.mkdir()
    (TEST_DIR / "file1.jpg").touch()
    (TEST_DIR / "subdir").mkdir()
    (TEST_DIR / "subdir" / "file2.jpg").touch()
    (TEST_DIR / "subdir" / "nested").mkdir()
    (TEST_DIR / "subdir" / "nested" / "file3.png").touch()

def teardown():
    if TEST_DIR.exists():
        rmtree(TEST_DIR)

def test_recursive():
    cb = ConfigBuilder()
    # Test recursive=True
    files = cb._path_walk(str(TEST_DIR), recursive=True)
    files = [Path(f).relative_to(TEST_DIR).as_posix() for f in files]
    
    print(f"Recursive=True files found: {files}")
    
    expected = {'file1.jpg', 'subdir/file2.jpg', 'subdir/nested/file3.png'}
    assert set(files) == expected, f"Expected {expected}, got {set(files)}"
    
    # Test recursive=False
    files_non_rec = cb._path_walk(str(TEST_DIR), recursive=False)
    files_non_rec = [Path(f).relative_to(TEST_DIR).as_posix() for f in files_non_rec]
    
    print(f"Recursive=False files found: {files_non_rec}")
    
    expected_non_rec = {'file1.jpg'}
    assert set(files_non_rec) == expected_non_rec, f"Expected {expected_non_rec}, got {set(files_non_rec)}"

if __name__ == "__main__":
    try:
        setup()
        test_recursive()
    except AssertionError as e:
        print(f"TEST FAILED: {e}")
    except Exception as e:
        print(f"TEST ERROR: {e}")
    finally:
        teardown()
