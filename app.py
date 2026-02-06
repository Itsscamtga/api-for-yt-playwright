from fastapi import FastAPI, HTTPException, Query
from playwright.async_api import async_playwright
import asyncio
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Vidssave Session Generator API (GET)")


def cookies_to_netscape(cookies):
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        lines.append(
            "\t".join([
                c["domain"],
                "TRUE" if c["domain"].startswith(".") else "FALSE",
                c["path"],
                "TRUE" if c["secure"] else "FALSE",
                str(c["expires"] or 0),
                c["name"],
                c["value"],
            ])
        )
    return "\n".join(lines)


@app.get("/vidssave")
async def generate_session(
    youtube_url: str = Query(..., description="YouTube video URL"),
    quality: str = Query("360P", description="Video quality e.g. 360P, 720P"),
):
    logger.info(f"Starting session generation for URL: {youtube_url}, Quality: {quality}")
    parse_payload = None
    download_url = None

    async with async_playwright() as p:
        logger.info("Launching Chromium browser in headless mode")
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        logger.info("Browser launched successfully")

        logger.info("Creating new browser context and page")
        context = await browser.new_context()
        page = await context.new_page()
        logger.info("Browser context and page created")

        # Capture origin=cache parse request
        def capture_request(req):
            nonlocal parse_payload
            if "/media/parse" in req.url and req.method == "POST":
                body = req.post_data or ""
                if "origin=cache" in body:
                    logger.info("Captured parse payload from request")
                    parse_payload = req.post_data

        logger.info("Setting up request interceptor")
        page.on("request", capture_request)

        # Open site and submit URL
        logger.info("Navigating to vidssave.com")
        await page.goto("https://vidssave.com/", timeout=60000)
        logger.info("Page loaded successfully")
        
        logger.info("Waiting for input field")
        await page.wait_for_selector("input", timeout=20000)
        logger.info(f"Filling input field with URL: {youtube_url}")
        await page.fill("input", youtube_url)

        logger.info("Looking for submit button")
        for btn in await page.locator("button").all():
            if await btn.is_visible():
                logger.info("Clicking submit button")
                await btn.click()
                break

        logger.info("Waiting 6 seconds for response")
        await asyncio.sleep(6)

        if not parse_payload:
            logger.error("Failed to capture parse payload")
            await browser.close()
            raise HTTPException(500, "Failed to capture parse payload")
        
        logger.info("Parse payload captured successfully")

        # Call parse API inside SAME browser session
        logger.info("Calling parse API with captured payload")
        response = await page.evaluate(
            """
            async (payload) => {
                const r = await fetch(
                    "https://api.vidssave.com/api/contentsite_api/media/parse",
                    {
                        method: "POST",
                        headers: {
                            "content-type": "application/x-www-form-urlencoded"
                        },
                        body: payload
                    }
                );
                return await r.json();
            }
            """,
            parse_payload,
        )
        logger.info("Parse API response received")

        resources = response.get("data", {}).get("resources", [])
        logger.info(f"Found {len(resources)} resources in response")

        logger.info(f"Searching for video with quality: {quality}")
        for r in resources:
            if (
                r.get("type") == "video"
                and r.get("format") == "MP4"
                and r.get("quality") == quality
                and r.get("download_mode") == "direct"
            ):
                download_url = r["download_url"]
                logger.info(f"Found matching download URL for quality {quality}")
                break

        if not download_url:
            logger.error(f"Direct download URL not found for quality {quality}")
            await browser.close()
            raise HTTPException(404, "Direct download URL not found")
        
        logger.info("Download URL found successfully")

        logger.info("Extracting cookies from browser context")
        cookies_json = await context.cookies()
        logger.info(f"Extracted {len(cookies_json)} cookies")
        
        logger.info("Converting cookies to Netscape format")
        cookies_netscape = cookies_to_netscape(cookies_json)

        logger.info("Closing browser")
        await browser.close()
        logger.info("Browser closed successfully")

    logger.info("Session generation completed successfully")
    return {
        "download_url": download_url,
        "cookies": cookies_json,
        "cookies_netscape": cookies_netscape
    }

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app)
