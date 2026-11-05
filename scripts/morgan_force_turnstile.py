#!/usr/bin/env python3
"""Force-render a visible Turnstile checkbox on the live CDP session and wait for token."""
from __future__ import annotations

import asyncio
import json
from playwright.async_api import async_playwright


async def main() -> None:
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp("http://127.0.0.1:9333")
        page = b.contexts[0].pages[0]

        result = await page.evaluate(
            """() => new Promise((resolve) => {
              const host = document.querySelector('.mt-4.flex.justify-center');
              if (!host) { resolve({err:'no host'}); return; }
              host.innerHTML = '';
              const mount = document.createElement('div');
              host.appendChild(mount);
              const inp = document.createElement('input');
              inp.type = 'hidden';
              inp.name = 'cf-turnstile-response';
              inp.id = 'cf-forced-response';
              host.appendChild(inp);
              if (typeof turnstile === 'undefined') { resolve({err:'no turnstile'}); return; }
              try {
                const wid = turnstile.render(mount, {
                  sitekey: '0x4AAAAAAEkSMulb_IeE7kco',
                  theme: 'light',
                  size: 'normal',
                  appearance: 'always',
                  execution: 'render',
                  retry: 'auto',
                  callback: (token) => {
                    inp.value = token;
                    const btn = document.querySelector('button[type=submit]');
                    if (btn) btn.disabled = false;
                    resolve({ok:true, tokenLen: token.length, wid});
                  },
                  'error-callback': (err) => resolve({ok:false, error: String(err), wid}),
                  'expired-callback': () => {},
                  'timeout-callback': () => resolve({ok:false, error:'timeout-callback', wid}),
                });
                // fallback resolve if neither callback fires quickly — keep waiting via outer loop
                setTimeout(() => resolve({pending:true, wid, framesHint:'check frames'}), 5000);
              } catch (e) {
                resolve({err: String(e)});
              }
            })"""
        )
        print("render", json.dumps(result))

        for i in range(60):
            frames = [f.url for f in page.frames if "challenge" in f.url or "turnstile" in f.url]
            st = await page.evaluate(
                """() => {
                  const tok = document.querySelector('input[name=cf-turnstile-response]');
                  const btn = document.querySelector('button[type=submit]');
                  let resp='';
                  try { resp = turnstile.getResponse() || ''; } catch(e) {}
                  return {
                    tokLen: tok?(tok.value||'').length:0,
                    respLen: resp.length,
                    btnDisabled: btn?btn.disabled:null,
                    host: document.querySelector('.mt-4.flex.justify-center')?.innerHTML?.slice(0,200)
                  };
                }"""
            )
            print(i, "frames", len(frames), st)
            if frames:
                f = next(fr for fr in page.frames if fr.url in frames or "turnstile" in fr.url or "challenge" in fr.url)
                for sel in ('input[type="checkbox"]', '[role="checkbox"]', "label", "body"):
                    try:
                        await f.locator(sel).first.click(timeout=1500, force=True)
                        print("clicked", sel)
                        break
                    except Exception as exc:
                        print("click fail", sel, type(exc).__name__)
            if st["tokLen"] > 20 or st["btnDisabled"] is False:
                print("READY")
                # submit
                try:
                    async with page.expect_response(
                        lambda r: r.request.method == "POST" and "sign-in" in r.url, timeout=45000
                    ) as ri:
                        await page.locator('button[type="submit"]').click()
                    resp = await ri.value
                    print("sign-in", resp.status, (await resp.text())[:400])
                except Exception as exc:
                    print("submit err", exc)
                    await page.locator('button[type="submit"]').click(force=True)
                await page.wait_for_timeout(4000)
                print("url", page.url)
                if "/sign-in" not in page.url:
                    await page.context.storage_state(path="logs/morgan_storage_state.json")
                    print("STORAGE_SAVED")
                return
            await asyncio.sleep(2)
        print("FAILED")


if __name__ == "__main__":
    asyncio.run(main())
