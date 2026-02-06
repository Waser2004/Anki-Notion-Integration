"""Simple script to fetch and print page content from Notion using the NotionClient."""

import json
import sys
from src.anki_notion_integration.notion_client import NotionClient


def main():
    """Main function to fetch and print page content."""
    # Get API key from user
    api_key = input("Enter your Notion API key: ").strip()
    if not api_key:
        print("Error: API key is required.")
        sys.exit(1)
    
    # Get page ID from user
    page_id = input("Enter the page ID: ").strip()
    if not page_id:
        print("Error: Page ID is required.")
        sys.exit(1)
    
    try:
        # Create NotionClient instance
        client = NotionClient(api_token=api_key)
        
        # Fetch page content
        print(f"\nFetching content for page {page_id}...")
        page_content = client.get_page_content(page_id)
        print(page_content)
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
