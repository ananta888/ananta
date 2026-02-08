import os
import time
import pytest
from unittest.mock import MagicMock, patch
from agent.utils import _cleanup_old_backups
from agent.config import settings

def test_cleanup_old_backups(tmp_path):
    # Setup: Create a temporary backup directory
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    
    # Create some "old" and "new" files
    old_file = backup_dir / "old_backup.sql"
    new_file = backup_dir / "new_backup.sql"
    
    old_file.write_text("old content")
    new_file.write_text("new content")
    
    # Set modification times
    now = time.time()
    old_time = now - (20 * 86400) # 20 days old
    new_time = now - (5 * 86400)  # 5 days old
    
    os.utime(old_file, (old_time, old_time))
    os.utime(new_file, (new_time, new_time))
    
    # Mock settings and get_data_dir
    with patch("agent.utils.get_data_dir", return_value=str(tmp_path)), \
         patch("agent.utils.settings") as mock_settings:
        
        mock_settings.backups_retention_days = 14
        
        # Run cleanup
        _cleanup_old_backups()
        
        # Verify
        assert not old_file.exists()
        assert new_file.exists()

def test_cleanup_no_dir(tmp_path):
    # Test should not fail if directory doesn't exist
    with patch("agent.utils.get_data_dir", return_value=str(tmp_path / "nonexistent")):
        _cleanup_old_backups() # Should not raise exception
