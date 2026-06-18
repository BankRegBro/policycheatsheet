# BankRegWire — repo update

Unzip at the ROOT of your repo, overwriting when prompted. It overlays only the
files that changed or are new; files not in here are left untouched.

## Commit it (from a local clone, after unzipping over it)
    git pull
    git add -A
    git status            # sanity-check the changed/added list
    git commit -m "Video splash + glass hero + Call Report suite; home-link sweep; SEO"
    git push

## Notes
- Intentionally NOT included (you removed these): csbs-comment-letters.html,
  genius-act-stablecoin-regime.html, hr941-small-lender-act.html. If they still
  exist in the repo, delete them:  git rm <file>
- Video is the 720p (~8.3MB) as bankregwire-crossing.mp4. A 1080p exists if you
  want max quality over load speed.
- Domain assumed https://bankregwire.com/ in canonical/sitemap/robots — change if different.
- callreport/worker.js (FDIC proxy) and the regulatory-wire worker deploy to
  Cloudflare separately, not Pages. Unchanged here.
- After deploy, submit https://bankregwire.com/sitemap.xml in Google Search Console.
