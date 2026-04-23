# Data Download Manager Setup Guide

The OrderForge **Data Download Manager** is a visual UI feature built directly into the frontend. It allows you to browse, download, and manage gigabytes of Bitfinex market data stored securely in the team's Amazon S3 bucket (`orderforge-s3-bucket`), all without needing to use terminal commands.

Because the data is stored securely in a private S3 bucket, you must provide your own AWS credentials to use the manager.

---

## 1. Initial Setup: Generating AWS Keys

To connect the application to the S3 bucket, you need an AWS Access Key. If you have administrator access to the AWS console, you can generate your permanent keys in 30 seconds:

1. Log in to the [AWS Management Console](https://console.aws.amazon.com/).
2. Search for **"IAM"** in the top search bar and click it.
3. On the left sidebar, click **Users** and click on your specific username.
4. Open the **Security credentials** tab.
5. Scroll down to the **Access keys** section and click **Create access key**.
6. Select **Command Line Interface (CLI)** or **Application running outside AWS**, hit Next, and click **Create**.

> [!WARNING]
> This is the *only* time AWS will ever display your **Secret Access Key**. Do not close the window until you have completed Step 2!

## 2. Configuring the Application

We use a hidden file called `.env` to securely store your keys on your local machine. This file is highly protected—Git is configured to completely ignore it (via `.gitignore`), meaning there is zero risk of you accidentally pushing your secret keys to GitHub.

1. Open the root of the `orderForge` repository in your code editor.
2. Create a brand new text file named exactly `.env`.
3. Paste the keys you generated in Step 1 into this file using the exact format below:

```env
AWS_DEFAULT_REGION="eu-central-1"
AWS_ACCESS_KEY_ID="AKIAxxxxxxxxxxxxxxxx"
AWS_SECRET_ACCESS_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

You are completely done! The `python-dotenv` package installed in the backend will now securely and automatically stream these keys into memory every time you boot the server.

---

## 3. Using the Download Manager

Once your `.env` file is ready, launch the UI exactly as normal from your terminal:
```bash
.venv/bin/python main.py
```

1. Open `http://localhost:8000` in your web browser.
2. Under the **"Select Data Days"** grid, click the dashed tile labeled **`Manage Data...`**.
3. A popup will appear scanning both your hard drive and the remote S3 server:
    - **Remote:** Click **Download** to actively stream the 1GB+ day archive directly from S3 onto your Mac via a dedicated background thread.
    - **Installed:** Click **Delete** to instantly remove the massive dataset from your hard drive to free up extreme amounts of storage space once you are finished running a backtest.

> [!TIP]
> **Drive Space Optimization:** The Download Manager intentionally downloads the data as highly compressed `.tar.gz` archives and completely ignores them until the exact moment you hit "Run Backtest", ensuring massive gigabytes of uncompressed text don't sit stagnant on your machine.
