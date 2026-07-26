# Noteck: Overview

Noteck syncs content from Notion into Anki so you can maintain notes in Notion and study them in Anki.

## What you can do

- Select which Notion pages should sync.
- Create cards from Notion toggle blocks.
- Use multiple card types:
  - Basic
  - Basic (Reversed)
  - Input
  - Cloze (from inline and whole-block markers in paragraphs and advanced cloze containers)
- Exclude specific toggle cards from sync.
- Set page-level default card types.
- Open an Image Occlusion workflow for images on a page.

## First-time setup
1. Open the Notion add-on in Anki.
2. In Settings, paste your Notion API key:
  1. Visit https://www.notion.so/profile/integrations and create a new Internal Integration. Provide a name and select your workspace. (Optional) Add an icon for easier identification.
    
    ![image.png](attachment:476b1247-c6d4-4cee-9093-96ee15fc6a6d:image.png)
    
  2. In the integration settings, copy the Internal integration secret. Optionally restrict permissions — Noteck requires only the "Read content" capability. Save the integration.
    
    ![image.png](attachment:52040041-8d44-4b3f-a8a8-c2de0c12337f:image.png)
    
  3. Return to Anki, open `Notion` → `Settings`, and paste the copied secret into the `Notion API key` field.
    
    ![image.png](attachment:bb919968-b03b-4106-8e60-0582a6a0c56f:image.png)
    
3. In Notion, share the pages you want synced with the integration:
  1. On each page, open the menu (•••), select Connections, click Add new Connection, and choose the integration you created.
    
    ![image.png](attachment:ee5512f4-06f4-40d0-a6c1-dd94fa70f4cb:image.png)
    
4. In Anki, open `Pages`, select the pages to include in syncing, and review the default card-type settings.
5. From `Settings`, run `Sync Notion now` to perform the initial synchronization.

## Daily workflow

1. Edit your source content in Notion.
2. Sync from the add-on.
3. Review newly created or updated cards in Anki.

## Important behavior

- Sync direction is one-way: Notion to Anki.
- Notion remains the source of truth for generated content.
- Image Occlusion cards are launched manually from the Image Occlusion tab.
