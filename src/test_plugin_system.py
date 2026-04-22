#!/usr/bin/env python3

"""
Test script to verify the plugin system works correctly.
This tests if plugins can be loaded and commands registered properly.
"""

import sys
import os

# Add current directory to path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_plugin_loading():
    """Test that plugins load correctly"""
    print("Testing plugin loading system...")
    
    try:
        # Import commands which handles plugin loading
        import commands
        
        # Force load plugins
        commands.load_plugins()
        
        print(f"Loaded {len(commands.COMMANDS)} commands")
        
        # Check if some expected commands exist
        expected_commands = ['help', 'admin', 'lockdown', 'stats']
        for cmd in expected_commands:
            if cmd in commands.COMMANDS:
                print(f"✓ Command '{cmd}' found")
            else:
                print(f"✗ Command '{cmd}' NOT found")
                
        return True
        
    except Exception as e:
        print(f"Error testing plugin system: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    test_plugin_loading()