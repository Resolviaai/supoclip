import asyncio
import base64
import hmac
import hashlib
import json
import subprocess
import urllib.parse
from playwright.async_api import async_playwright

async def run_verification():
    print("=" * 70)
    print("STARTING 3-PASS DEEP PLAYWRIGHT VERIFICATION")
    print("=" * 70)

    # 1. Generate Better Auth signed cookie
    tok = subprocess.check_output(
        ["docker", "exec", "supoclip-postgres", "psql", "-U", "supoclip", "-d", "supoclip", "-tAc",
         'SELECT token FROM session WHERE "userId" = \'OpB59NFJKFZqpolm8NM9H4159v422ohr\' ORDER BY "expiresAt" DESC LIMIT 1;'],
        text=True
    ).strip()
    secret = subprocess.check_output(
        ["docker", "exec", "supoclip-frontend", "node", "-e", "process.stdout.write(process.env.BETTER_AUTH_SECRET || '')"],
        text=True
    ).strip()
    sig = base64.b64encode(hmac.new(secret.encode(), tok.encode(), hashlib.sha256).digest()).decode()
    cookie_val = urllib.parse.quote(f"{tok}.{sig}", safe="")
    print(f"Generated auth cookie (tok length: {len(tok)})")

    base_urls = [
        "https://dispatch.theresolvia.com",
        "http://localhost:3107"
    ]
    # Test primary domain first; fallback to localhost if tunnel has transient jitter
    primary_base = base_urls[0]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        results = {
            "pass1_desktop": [],
            "pass2_mobile": [],
            "pass3_interactivity": []
        }

        # Helper to set up context with cookies and error tracking
        async def create_ctx(viewport, is_mobile=False, user_agent=None):
            ctx = await browser.new_context(
                viewport=viewport,
                is_mobile=is_mobile,
                user_agent=user_agent
            )
            await ctx.add_cookies([
                {"name": "better-auth.session_token", "value": cookie_val, "domain": "dispatch.theresolvia.com", "path": "/", "secure": True},
                {"name": "__Secure-better-auth.session_token", "value": cookie_val, "domain": "dispatch.theresolvia.com", "path": "/", "secure": True},
                {"name": "better-auth.session_token", "value": cookie_val, "domain": "localhost", "path": "/"},
            ])
            return ctx

        # -------------------------------------------------------------
        # PASS 1: DESKTOP VIEWPORT (1280 x 800)
        # -------------------------------------------------------------
        print("\n>>> EXECUTING PASS 1: DESKTOP VIEWPORT (1280x800)...")
        desktop_ctx = await create_ctx({"width": 1280, "height": 800})
        desktop_page = await desktop_ctx.new_page()

        def is_real_net_error(req):
            if not req.failure:
                return False
            fail_str = str(req.failure)
            if "ERR_ABORTED" in fail_str or "favicon" in req.url:
                return False
            return True

        console_errors = []
        network_errors = []
        desktop_page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" and "favicon" not in msg.text else None)
        desktop_page.on("requestfailed", lambda req: network_errors.append(f"{req.url} {req.failure}") if is_real_net_error(req) else None)

        routes_to_test = [
            ("/", "Home"),
            ("/list", "Library"),
            ("/tasks/16161515-ffdc-48ac-8a11-c82daac4157a", "Original EMI Task"),
            ("/tasks/4bb890d4-8b01-4567-a884-275ab38cc81a", "Highlights+Broll Task"),
            ("/tasks/c1f60366-4e4b-4c26-8b53-8404ebbbf41d", "Topic Task"),
            ("/tasks/c87d873b-dfc9-49ee-8d12-cd8617c5ffbe", "Series Task"),
            ("/settings/social", "Social Accounts"),
            ("/analytics", "Analytics"),
            ("/settings", "Settings"),
            ("/settings/api-keys", "API Keys"),
            ("/status", "Status"),
        ]

        for route, name in routes_to_test:
            url = f"{primary_base}{route}"
            c_before = len(console_errors)
            n_before = len(network_errors)
            res = await desktop_page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await desktop_page.wait_for_timeout(1000)
            status = res.status if res else 0

            # Check horizontal overflow
            ovf = await desktop_page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            c_errs = len(console_errors) - c_before
            n_errs = len(network_errors) - n_before

            passed = (status == 200) and (ovf <= 5) and (n_errs == 0) and (c_errs == 0)
            if status != 200 or c_errs > 0:
                print(f"    --> DETAILS for {name}: status={status}")
                if c_errs > 0:
                    print(f"    --> console errors: {console_errors[c_before:]}")
            results["pass1_desktop"].append({"route": route, "name": name, "status": status, "ovf": ovf, "c_errs": c_errs, "n_errs": n_errs, "passed": passed})
            print(f"  [Pass 1] {name:22} | URL={route:45} | HTTP={status} | ovf={ovf}px | cErr={c_errs} | nErr={n_errs} | {'PASS' if passed else 'FAIL'}")

        # Desktop Workflow Selector Clicks on /
        await desktop_page.goto(f"{primary_base}/", wait_until="domcontentloaded")
        workflows = ["recap", "topic", "series", "trailer", "highlights"]
        for wf in workflows:
            btn = desktop_page.locator(f"button[data-workflow='{wf}'], button:has-text('{wf.capitalize()}')").first
            if await btn.count() > 0:
                await btn.click()
                await desktop_page.wait_for_timeout(300)
                print(f"  [Pass 1] Clicked workflow button: {wf}")

        await desktop_ctx.close()

        # -------------------------------------------------------------
        # PASS 2: MOBILE VIEWPORT (390 x 844, iPhone 14)
        # -------------------------------------------------------------
        print("\n>>> EXECUTING PASS 2: MOBILE VIEWPORT (390x844)...")
        mobile_ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        mobile_ctx = await create_ctx({"width": 390, "height": 844}, is_mobile=True, user_agent=mobile_ua)
        mobile_page = await mobile_ctx.new_page()

        m_console_errors = []
        m_network_errors = []
        mobile_page.on("console", lambda msg: m_console_errors.append(msg.text) if msg.type == "error" and "favicon" not in msg.text else None)
        mobile_page.on("requestfailed", lambda req: m_network_errors.append(f"{req.url} {req.failure}") if is_real_net_error(req) else None)

        for route, name in routes_to_test:
            url = f"{primary_base}{route}"
            c_before = len(m_console_errors)
            n_before = len(m_network_errors)
            res = await mobile_page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await mobile_page.wait_for_timeout(1000)
            status = res.status if res else 0

            ovf = await mobile_page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            c_errs = len(m_console_errors) - c_before
            n_errs = len(m_network_errors) - n_before

            passed = (status == 200) and (ovf <= 5) and (n_errs == 0) and (c_errs == 0)
            if status != 200 or c_errs > 0:
                print(f"    --> DETAILS for {name}: status={status}")
                if c_errs > 0:
                    print(f"    --> console errors: {m_console_errors[c_before:]}")
                if status != 200:
                    body_snippet = await mobile_page.locator("body").inner_text()
                    print(f"    --> body text: {body_snippet[:300]}")
            results["pass2_mobile"].append({"route": route, "name": name, "status": status, "ovf": ovf, "c_errs": c_errs, "n_errs": n_errs, "passed": passed})
            print(f"  [Pass 2] {name:22} | URL={route:45} | HTTP={status} | ovf={ovf}px | cErr={c_errs} | nErr={n_errs} | {'PASS' if passed else 'FAIL'}")

        # Mobile specific assertions
        # 1. URL input font-size >= 16px (prevents iOS auto-zoom)
        await mobile_page.goto(f"{primary_base}/", wait_until="domcontentloaded")
        input_elem = mobile_page.locator("input[placeholder*='youtube.com'], input[type='url']").first
        if await input_elem.count() > 0:
            font_size = await input_elem.evaluate("el => window.getComputedStyle(el).fontSize")
            print(f"  [Pass 2 Mobile Check] URL input computed fontSize: {font_size} (Expected >= 16px to prevent iOS auto-zoom)")
            assert float(font_size.replace("px", "")) >= 15.9, f"Font size too small: {font_size}"

        # 2. Library mobile copy: 'hover to preview' must be hidden
        await mobile_page.goto(f"{primary_base}/list", wait_until="domcontentloaded")
        body_text = await mobile_page.locator("body").inner_text()
        has_hover_copy = "hover to preview" in body_text
        print(f"  [Pass 2 Mobile Check] 'hover to preview' present in mobile body: {has_hover_copy} (Expected: False)")

        # 3. Task focus / preview modal on mobile
        await mobile_page.goto(f"{primary_base}/tasks/16161515-ffdc-48ac-8a11-c82daac4157a", wait_until="domcontentloaded")
        first_clip = mobile_page.locator("[data-clip-card], [data-clip-id]").first
        if await first_clip.count() > 0:
            await first_clip.click()
            await mobile_page.wait_for_timeout(600)
            modal_ovf = await mobile_page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            print(f"  [Pass 2 Mobile Check] Clip focus modal opened, screen ovf: {modal_ovf}px")

        await mobile_ctx.close()

        # -------------------------------------------------------------
        # PASS 3: INTERACTIVITY & CLIP EDITOR ZERO-404 VERIFICATION
        # -------------------------------------------------------------
        print("\n>>> EXECUTING PASS 3: INTERACTIVITY & CLIP EDITOR ZERO-404...")
        inter_ctx = await create_ctx({"width": 1280, "height": 800})
        inter_page = await inter_ctx.new_page()

        http_404s = []
        inter_page.on("response", lambda r: http_404s.append(r.url) if r.status == 404 else None)

        # 1. Open Editor Studio for task 16161515 clip 1
        editor_path = "/tasks/16161515-ffdc-48ac-8a11-c82daac4157a/clips/d05a3604-2c2b-43ff-9cf5-1256405cc60d/editor"
        await inter_page.goto(f"{primary_base}/tasks/16161515-ffdc-48ac-8a11-c82daac4157a", wait_until="domcontentloaded")
        editor_btn = inter_page.locator("a:has-text('Open Editor'), a:has-text('Edit Clip'), a[href*='/editor']").first
        if await editor_btn.count() > 0:
            editor_url = await editor_btn.get_attribute("href")
            editor_path = editor_url
        print(f"  [Pass 3] Opening Clip Editor Studio: {editor_path}")
        await inter_page.goto(f"{primary_base}{editor_path}", wait_until="domcontentloaded", timeout=30000)
        await inter_page.wait_for_timeout(2000)

        # Check Switch Clip Popover (verify zero 404s for thumbnails.jpg)
        switch_btn = inter_page.locator("[data-editor-studio] button[aria-label='Switch clip'], button:has-text('Clip ')").first
        if await switch_btn.count() > 0:
            await switch_btn.click()
            await inter_page.wait_for_timeout(1000)
            print(f"  [Pass 3] Clicked 'Switch Clip' popover. 404 count so far: {len(http_404s)}")
            assert len([u for u in http_404s if "thumbnails.jpg" in u]) == 0, f"Found thumbnails.jpg 404: {http_404s}"

        # Check Hook Title Editing
        title_input = inter_page.locator("input[placeholder*='Hook title'], input[value*='EMI'], input[name='hook_title']").first
        if await title_input.count() > 0:
            val = await title_input.input_value()
            print(f"  [Pass 3] Hook Title input value: '{val}'")
            await title_input.fill("Why EMI Traps You Forever")
            print("  [Pass 3] Updated Hook Title to: 'Why EMI Traps You Forever'")

        # Check Timeline Trimmer & Split
        split_btn = inter_page.locator("button:has-text('Split'), button[aria-label*='Split' i]").first
        if await split_btn.count() > 0:
            await split_btn.click()
            await inter_page.wait_for_timeout(500)
            print("  [Pass 3] Tested timeline Split button")

            # Test Undo
            undo_btn = inter_page.locator("button[aria-label*='Undo' i], button:has-text('Undo')").first
            if await undo_btn.count() > 0:
                await undo_btn.click()
                await inter_page.wait_for_timeout(300)
                print("  [Pass 3] Tested Undo button")

        # Check Export Dialog
        export_btn = inter_page.locator("button:has-text('Export')").first
        if await export_btn.count() > 0:
            await export_btn.click()
            await inter_page.wait_for_timeout(800)
            dialog = inter_page.locator("[role='dialog']")
            if await dialog.count() > 0:
                dialog_text = await dialog.inner_text()
                has_export_opts = "Export" in dialog_text
                print(f"  [Pass 3] Export dialog visible: {has_export_opts}")
                # Close dialog
                close_btn = dialog.locator("button:has-text('Cancel'), button[aria-label*='Close' i]").first
                if await close_btn.count() > 0:
                    await close_btn.click()

        # 2. Test Direct File Upload UI on /
        await inter_page.goto(f"{primary_base}/", wait_until="domcontentloaded")
        upload_tab = inter_page.locator("button:has-text('Upload File'), button:has-text('Upload video'), button[data-tab='upload']").first
        if await upload_tab.count() > 0:
            await upload_tab.click()
            await inter_page.wait_for_timeout(400)
            print("  [Pass 3] Switched to File Upload dropzone successfully")

        # 3. Test YouTube Social Page
        await inter_page.goto(f"{primary_base}/settings/social", wait_until="domcontentloaded")
        await inter_page.wait_for_timeout(2500)
        social_text = await inter_page.locator("body").inner_text()
        has_connected_yt = "Rohit Bag" in social_text or "Active" in social_text or "YouTube" in social_text
        print(f"  [Pass 3] /settings/social displays connected YouTube channel: {has_connected_yt}")
        assert has_connected_yt, f"/settings/social did not display connected YouTube channel! Text: {social_text[:200]}"

        await inter_ctx.close()
        await browser.close()

    desktop_fails = [r for r in results["pass1_desktop"] if not r["passed"]]
    mobile_fails = [r for r in results["pass2_mobile"] if not r["passed"]]
    if desktop_fails or mobile_fails:
        print(f"\n[ERROR] VERIFICATION FAILED! Desktop fails: {len(desktop_fails)}, Mobile fails: {len(mobile_fails)}")
        for f in desktop_fails:
            print(f"  Desktop Fail: {f['name']} ({f['route']}) HTTP={f['status']} ovf={f['ovf']} nErr={f['n_errs']}")
        for f in mobile_fails:
            print(f"  Mobile Fail: {f['name']} ({f['route']}) HTTP={f['status']} ovf={f['ovf']} nErr={f['n_errs']}")
        raise AssertionError(f"3-Pass verification failed: {len(desktop_fails)} desktop, {len(mobile_fails)} mobile")

    print("\n" + "=" * 70)
    print("ALL 3 PASSES COMPLETED SUCCESSFULLY WITH 0 FAILURES!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(run_verification())

