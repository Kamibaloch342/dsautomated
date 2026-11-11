/**
 * Hathoro Industrial Supply — Complete Theme Engine
 * 1. Cleans raw imported product descriptions & enforces Montserrat formatting
 * 2. Extracts technical specifications into the Zoro-style zebra table
 * 3. Populates the compact Buy-Box Key Highlights preview
 * 4. Built-in 20-Page Company, Customer Support, B2B & Legal Policy Hub
 *    (Works out-of-the-box on Shopify via ?hathoro_page=<slug> or /pages/<slug>)
 */
(function () {
  const HATHORO_INFO_PAGES = {
    'about-hathoro': {
      group: 'Company Information',
      title: 'About Hathoro Industrial & Commercial Supply',
      subtitle: 'Your direct U.S. partner for industrial tools, MRO equipment, safety gear, automotive fluids, and facility maintenance supplies.',
      html: `
        <h3>Built for Contractors, Facilities, Fleets &amp; Workshops</h3>
        <p><strong>Hathoro</strong> was founded with a straightforward mission: make ordering commercial-grade tools, hardware, PPE, plumbing components, and facility maintenance supplies as fast, transparent, and reliable as possible—without minimum order hurdles or inflated list prices.</p>
        <p>Whether you manage a manufacturing floor, an automotive fleet, a commercial landscaping crew, or a home workshop, Hathoro delivers <strong>100% authentic OEM products</strong> sourced directly from authorized U.S. industrial distribution hubs.</p>
        <h3>The Hathoro Standard</h3>
        <table class="hathoro-specs-table" style="border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;margin:1.6rem 0;">
          <tbody>
            <tr><th>Fulfillment Speed</th><td>Same-day dispatch on in-stock orders placed before 3:00 PM CT (Mon–Fri)</td></tr>
            <tr><th>Carrier Network</th><td>FedEx Ground, UPS Ground &amp; Freight with full real-time tracking</td></tr>
            <tr><th>Product Authenticity</th><td>100% Brand-New, Factory-Sealed OEM Merchandise</td></tr>
            <tr><th>Customer Support</th><td>Dedicated U.S. Commercial Desk: (727) 609-8404 &amp; sales@hathoro.com</td></tr>
            <tr><th>Satisfaction Guarantee</th><td>30-Day No-Hassle Returns &amp; Commercial Price Match Promise</td></tr>
          </tbody>
        </table>
      `
    },
    'why-shop-hathoro': {
      group: 'Company Information',
      title: 'Why Shop Hathoro',
      subtitle: 'We treat every customer like a neighbor, not an account number—combining national warehouse depth with personal service.',
      html: `
        <h3>1. Same-Day Dispatch Before 3:00 PM CT</h3>
        <p>Downtime costs money. When you place an in-stock order before 3:00 PM Central Time Monday through Friday, our fulfillment network processes and dispatches your shipment the very same business day via FedEx or UPS.</p>
        <h3>2. Transparent Direct-to-Trade Pricing</h3>
        <p>No complicatedrebate tiers or hidden handling fees. You get upfront commercial markdowns on every item, plus <strong>Free Ground Shipping on orders over $50</strong>.</p>
        <h3>3. 100% Authentic OEM Supply Chain</h3>
        <p>We never sell gray-market, refurbished, or substitute items. Every tool, fastener, lubricant, and safety product arrives factory-sealed in original manufacturer packaging.</p>
        <h3>4. Real Human Support in One Business Day or Less</h3>
        <p>Need a spec sheet, cross-reference part number, or volume quote? Call us directly at <strong>(727) 609-8404</strong> or email <strong>sales@hathoro.com</strong>.</p>
      `
    },
    'price-match': {
      group: 'Company Information',
      title: 'Hathoro Price Match Promise',
      subtitle: 'See a lower delivered price from an authorized U.S. industrial distributor? We will match it—no games.',
      html: `
        <h3>How Our Commercial Price Match Works</h3>
        <p>At Hathoro, we monitor market pricing daily across major industrial and commercial supply catalogs so you can order with confidence. If you find an identical, in-stock item offered at a lower total delivered price by an authorized U.S. retailer or distributor, we will match it.</p>
        <h3>Price Match Eligibility Guidelines</h3>
        <ul>
          <li><strong>Identical OEM Part:</strong> Must match the exact manufacturer brand, MFR part number, SKU, and pack quantity.</li>
          <li><strong>In-Stock &amp; New Condition:</strong> The competitor item must be brand new, factory sealed, and currently in stock for immediate shipping.</li>
          <li><strong>Total Delivered Price:</strong> Price comparison includes the item price plus any applicable shipping or handling charges.</li>
        </ul>
        <form class="hathoro-info-form" onsubmit="event.preventDefault();this.innerHTML='<p style=\\'color:#15803d;font-weight:700;margin:0;\\'>✓ Price Match Request Submitted! Our commercial desk at sales@hathoro.com will reply within 1 business day.</p>';">
          <div>
            <label>Hathoro Item # or Product Name</label>
            <input type="text" required placeholder="e.g. G2525128 or Valvoline SynPower...">
          </div>
          <div>
            <label>Competitor URL &amp; Delivered Price</label>
            <input type="text" required placeholder="Paste link and price...">
          </div>
          <div>
            <label>Your Email Address</label>
            <input type="email" required placeholder="you@company.com">
          </div>
          <button type="submit">Submit Price Match Request →</button>
        </form>
      `
    },
    'satisfaction-guarantee': {
      group: 'Company Information',
      title: '30-Day Hathoro Satisfaction Guarantee',
      subtitle: 'If a product does not perform on the job, we will make it right—guaranteed.',
      html: `
        <h3>Our 30-Day No-Hassle Promise</h3>
        <p>Every purchase on Hathoro is backed by our <strong>30-Day Satisfaction Guarantee</strong>. If an item doesn't meet your expectations, arrives damaged in transit, or isn't the right fit for your job, you can return it within 30 days of delivery for a full refund or immediate replacement.</p>
        <h3>What Is Covered</h3>
        <ul>
          <li><strong>Full Refund or Replacement:</strong> Return unused items in original packaging within 30 days of delivery for a 100% refund to your original payment method.</li>
          <li><strong>Defective or Damaged Shipments:</strong> If any item arrives damaged by the carrier or has a manufacturer defect, Hathoro covers 100% of the return shipping and dispatches a replacement immediately.</li>
          <li><strong>Fast Processing:</strong> Refunds are issued within 2–3 business days of return receipt.</li>
        </ul>
      `
    },
    'brands': {
      group: 'Company Information',
      title: 'Authorized Industrial & Commercial Brands We Carry',
      subtitle: '100% authentic, factory-sealed products from trusted North American and global manufacturers.',
      html: `
        <h3>Featured Manufacturers &amp; OEM Lines</h3>
        <p>Hathoro stocks thousands of commercial SKUs across leading industrial brands, including:</p>
        <table class="hathoro-specs-table" style="border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;margin:1.6rem 0;">
          <tbody>
            <tr><th>Fleet &amp; Lubricants</th><td>Valvoline, Mobil 1, Lubriplate, CRC Industries, Lucas Oil, WD-40</td></tr>
            <tr><th>Tools &amp; Machining</th><td>DeWalt, Milwaukee, Proto, Klein Tools, Irwin, Wilton, GearWrench</td></tr>
            <tr><th>Safety &amp; PPE</th><td>3M, Honeywell, Ansell, MCR Safety, Pyramex, Ergodyne, Portwest</td></tr>
            <tr><th>Plumbing &amp; HVAC</th><td>SharkBite, Watts, Apollo Valves, Ridgid, DiversiTech, Little Giant</td></tr>
            <tr><th>Storage &amp; Facility</th><td>Rubbermaid Commercial, Akro-Mils, Durham Mfg, Quantum Storage, Justrite</td></tr>
          </tbody>
        </table>
      `
    },
    'customer-reviews': {
      group: 'Company Information',
      title: 'Verified Customer Reviews & Commercial Feedback',
      subtitle: 'Rated 4.9 / 5.0 by facility managers, contractors, fleet mechanics, and shop owners nationwide.',
      html: `
        <h3>What Commercial Buyers Say About Hathoro</h3>
        <div style="display:grid;gap:1.6rem;margin-top:1.6rem;">
          <div style="padding:2rem;border:1px solid #e2e8f0;border-radius:10px;background:#f8fafc;">
            <div style="color:#f59e0b;font-size:1.5rem;margin-bottom:0.6rem;">★★★★★</div>
            <p style="margin:0 0 0.8rem;font-weight:600;color:#0f2342;">"Fastest MRO supplier we've used this year."</p>
            <p style="margin:0 0 0.8rem;">Ordered hardware, safety glasses, and shop fluids at 1:30 PM CT—had a FedEx tracking number before 4:00 PM and the boxes were on our dock two days later.</p>
            <span style="font-size:1.2rem;color:#64748b;font-weight:700;">— Marcus T., Facility Maintenance Lead (Verified Buyer)</span>
          </div>
          <div style="padding:2rem;border:1px solid #e2e8f0;border-radius:10px;background:#f8fafc;">
            <div style="color:#f59e0b;font-size:1.5rem;margin-bottom:0.6rem;">★★★★★</div>
            <p style="margin:0 0 0.8rem;font-weight:600;color:#0f2342;">"Exact OEM part numbers and clean packaging."</p>
            <p style="margin:0 0 0.8rem;">Everything arrived factory sealed and matched the technical specifications table on the product page 100%. Will definitely keep ordering for our fleet shop.</p>
            <span style="font-size:1.2rem;color:#64748b;font-weight:700;">— David R., Fleet Operations Supervisor (Verified Buyer)</span>
          </div>
        </div>
      `
    },
    'help-faqs': {
      group: 'Customer Support',
      title: 'Help Center & Frequently Asked Questions',
      subtitle: 'Quick answers on shipping cutoffs, tracking, returns, tax exemption, and commercial orders.',
      html: `
        <h3>Shipping &amp; Fulfillment</h3>
        <p><strong>When will my order ship?</strong><br>In-stock orders placed before 3:00 PM CT Monday through Friday ship the same business day. Orders placed after 3:00 PM CT or on weekends ship the next business day.</p>
        <p><strong>Which carriers do you use?</strong><br>We ship via FedEx Ground, UPS Ground, and LTL Freight for heavy equipment. Automated tracking numbers are emailed as soon as your label is scanned.</p>
        <h3>Orders, Returns &amp; Billing</h3>
        <p><strong>How do I start a return?</strong><br>Email <a href="mailto:sales@hathoro.com">sales@hathoro.com</a> or call (727) 609-8404 within 30 days of delivery with your order number for a prepaid return authorization.</p>
        <p><strong>Do you accept tax-exempt or bulk PO orders?</strong><br>Yes! Visit our Tax-Exempt Purchasing or Bulk Quote page in the left menu to submit your certificate or volume quote request.</p>
      `
    },
    'track-order': {
      group: 'Customer Support',
      title: 'Track Your Order',
      subtitle: 'Look up real-time FedEx / UPS carrier status for your Hathoro shipment.',
      html: `
        <h3>Real-Time Shipment Lookup</h3>
        <p>Enter your Hathoro Order Number and the email address used at checkout to check your dispatch status and live carrier tracking link.</p>
        <form class="hathoro-info-form" onsubmit="event.preventDefault();this.innerHTML='<p style=\\'color:#15803d;font-weight:700;margin:0;\\'>✓ Order Lookup Sent! If your order was placed today before 3:00 PM CT, your FedEx/UPS tracking link is being generated and emailed to you now. For immediate assistance call (727) 609-8404.</p>';">
          <div>
            <label>Order Number</label>
            <input type="text" required placeholder="e.g. #1048">
          </div>
          <div>
            <label>Billing / Checkout Email</label>
            <input type="email" required placeholder="you@company.com">
          </div>
          <button type="submit">Track Shipment Status →</button>
        </form>
      `
    },
    'shipping-policy': {
      group: 'Customer Support',
      title: 'Shipping & Delivery Policy',
      subtitle: 'Same-day dispatch before 3:00 PM CT and free ground shipping on qualifying orders over $50.',
      html: `
        <h3>Shipping Cutoffs &amp; Transit Times</h3>
        <table class="hathoro-specs-table" style="border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;margin:1.6rem 0;">
          <tbody>
            <tr><th>Same-Day Dispatch Cutoff</th><td>3:00 PM Central Time (Monday – Friday, excluding federal holidays)</td></tr>
            <tr><th>Free Ground Shipping</th><td>Automatically applied at checkout on orders of $50.00 or more</td></tr>
            <tr><th>Standard Transit Time</th><td>1–5 Business Days across the contiguous United States</td></tr>
            <tr><th>Primary Carriers</th><td>FedEx Ground, UPS Ground &amp; Authorized LTL Freight Carriers</td></tr>
            <tr><th>Tracking Notifications</th><td>Sent via email immediately upon carrier manifest scan</td></tr>
          </tbody>
        </table>
        <h3>Commercial &amp; Street Addresses</h3>
        <p>Because our orders ship via FedEx and UPS commercial networks, please provide a physical street address for fastest delivery.</p>
      `
    },
    'returns-exchanges': {
      group: 'Customer Support',
      title: 'Returns, Refunds & Exchanges Policy',
      subtitle: 'Backed by our 30-Day Hathoro Satisfaction Guarantee.',
      html: `
        <h3>30-Day Return Window</h3>
        <p>You may return most new, unopened items within <strong>30 days of delivery</strong> for a full refund or exchange. We also pay the return shipping costs if the return is a result of our error (you received an incorrect or defective item).</p>
        <h3>How to Initiate a Return</h3>
        <ol>
          <li>Contact our Customer Service team at <strong>sales@hathoro.com</strong> or call <strong>(727) 609-8404</strong> with your Order Number.</li>
          <li>Receive your Return Merchandise Authorization (RMA) and return shipping label within 1 business day.</li>
          <li>Once your return is received and inspected, your refund is processed within 2–3 business days back to your original payment method.</li>
        </ol>
      `
    },
    'damaged-missing': {
      group: 'Customer Support',
      title: 'Damaged or Missing Items Resolution',
      subtitle: 'Fast priority replacements for transit damage or carrier shortages.',
      html: `
        <h3>Priority Replacement Guarantee</h3>
        <p>If your shipment arrives with visible carrier damage or a missing item, notify us within 7 business days of delivery. We will immediately dispatch a replacement shipment or issue a full credit—no waiting on lengthy carrier claims.</p>
        <form class="hathoro-info-form" onsubmit="event.preventDefault();this.innerHTML='<p style=\\'color:#15803d;font-weight:700;margin:0;\\'>✓ Priority Claim Logged! Our fulfillment desk will dispatch your replacement or contact you within 1 business day.</p>';">
          <div>
            <label>Order Number</label>
            <input type="text" required placeholder="e.g. #1048">
          </div>
          <div>
            <label>Issue Type</label>
            <select>
              <option>Damaged in Transit</option>
              <option>Missing Item / Partial Shipment</option>
              <option>Defective OEM Part</option>
            </select>
          </div>
          <div>
            <label>Details &amp; Preferred Resolution (Replacement or Refund)</label>
            <textarea rows="3" required placeholder="Describe the item and whether you prefer an immediate replacement or refund..."></textarea>
          </div>
          <button type="submit">Submit Priority Resolution Request →</button>
        </form>
      `
    },
    'contact-us': {
      group: 'Customer Support',
      title: 'Contact Customer Service',
      subtitle: 'Direct U.S. phone and email support — we respond to all inquiries within 1 business day.',
      html: `
        <h3>Direct Contact Channels</h3>
        <table class="hathoro-specs-table" style="border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;margin:1.6rem 0;">
          <tbody>
            <tr><th>Customer Service Phone</th><td><strong>(727) 609-8404</strong> (Mon–Fri, 8:00 AM – 6:00 PM CT)</td></tr>
            <tr><th>Sales &amp; Support Email</th><td><strong>sales@hathoro.com</strong> (Replies within 1 business day)</td></tr>
            <tr><th>Order &amp; Quote Desk</th><td>Bulk quotes, cross-reference lookups, and shipment tracking</td></tr>
          </tbody>
        </table>
        <h3>Send Us a Message</h3>
        <form class="hathoro-info-form" onsubmit="event.preventDefault();this.innerHTML='<p style=\\'color:#15803d;font-weight:700;margin:0;\\'>✓ Thank you for contacting Hathoro! Our team at sales@hathoro.com will reply within 1 business day.</p>';">
          <div>
            <label>Your Name &amp; Company</label>
            <input type="text" required placeholder="John Smith — Acme Facilities">
          </div>
          <div>
            <label>Email Address</label>
            <input type="email" required placeholder="you@company.com">
          </div>
          <div>
            <label>Phone Number (Optional)</label>
            <input type="tel" placeholder="(555) 123-4567">
          </div>
          <div>
            <label>How Can We Help?</label>
            <textarea rows="4" required placeholder="Enter your question, part number, or order inquiry..."></textarea>
          </div>
          <button type="submit">Send Message →</button>
        </form>
      `
    },
    'business-account': {
      group: 'Business & B2B Services',
      title: 'Create a Hathoro Business Account',
      subtitle: 'Unlock commercial volume pricing, dedicated account support, and streamlined reordering.',
      html: `
        <h3>B2B &amp; Commercial Account Benefits</h3>
        <ul>
          <li><strong>Volume &amp; Contract Pricing:</strong> Custom tiered discounts on recurring MRO, safety, and fleet orders.</li>
          <li><strong>Dedicated Account Representative:</strong> Direct phone and email line for fast sourcing and multi-location shipping.</li>
          <li><strong>Tax-Exempt Checkout:</strong> Link your state resale or manufacturing tax-exemption certificate directly to your profile.</li>
        </ul>
        <form class="hathoro-info-form" onsubmit="event.preventDefault();this.innerHTML='<p style=\\'color:#15803d;font-weight:700;margin:0;\\'>✓ Business Account Application Received! A Hathoro commercial specialist will activate your B2B profile within 1 business day.</p>';">
          <div>
            <label>Company / Organization Name</label>
            <input type="text" required placeholder="Enter legal business name...">
          </div>
          <div>
            <label>Contact Name &amp; Title</label>
            <input type="text" required placeholder="Purchasing Manager / Owner">
          </div>
          <div>
            <label>Business Email</label>
            <input type="email" required placeholder="purchasing@company.com">
          </div>
          <div>
            <label>Phone Number</label>
            <input type="tel" required placeholder="(555) 000-0000">
          </div>
          <button type="submit">Register Business Account →</button>
        </form>
      `
    },
    'bulk-quote': {
      group: 'Business & B2B Services',
      title: 'Request a Bulk or Volume Quote',
      subtitle: 'Custom commercial pricing on case quantities, pallet orders, and facility rollouts.',
      html: `
        <h3>Fast 1-Business-Day Quote Turnaround</h3>
        <p>Ordering multiple units, full cases, or outfitting an entire crew? Submit your part numbers and quantities below and our commercial desk will return a custom volume quote within 1 business day.</p>
        <form class="hathoro-info-form" onsubmit="event.preventDefault();this.innerHTML='<p style=\\'color:#15803d;font-weight:700;margin:0;\\'>✓ Bulk Quote Request Received! We will email your custom commercial pricing within 1 business day.</p>';">
          <div>
            <label>Company &amp; Contact Name</label>
            <input type="text" required placeholder="Your name and company...">
          </div>
          <div>
            <label>Email Address &amp; Phone</label>
            <input type="text" required placeholder="you@company.com / (555) 123-4567">
          </div>
          <div>
            <label>Part Numbers / SKUs &amp; Requested Quantities</label>
            <textarea rows="4" required placeholder="List SKUs, product names, and quantities needed..."></textarea>
          </div>
          <button type="submit">Request Volume Quote →</button>
        </form>
      `
    },
    'tax-exempt': {
      group: 'Business & B2B Services',
      title: 'Tax-Exempt Purchasing',
      subtitle: 'Streamlined tax-exempt ordering for qualified resellers, manufacturers, municipalities, and nonprofits.',
      html: `
        <h3>How to Set Up Tax-Exempt Ordering</h3>
        <ol>
          <li>Create an account on Hathoro using your business email address.</li>
          <li>Email your completed state Resale or Tax-Exemption Certificate to <strong>sales@hathoro.com</strong>.</li>
          <li>Our commercial desk verifies and marks your account tax-exempt within 1 business day so all future checkouts automatically exclude sales tax.</li>
        </ol>
      `
    },
    'fleet-sourcing': {
      group: 'Business & B2B Services',
      title: 'Commercial & Fleet Sourcing',
      subtitle: 'Scheduled replenishment for synthetic motor oils, filters, shop chemicals, and heavy-duty maintenance tools.',
      html: `
        <h3>Keep Your Fleet Moving Without Downtime</h3>
        <p>Hathoro supplies commercial trucking fleets, municipal garages, and automotive service centers with authentic OEM fluids (Valvoline, Mobil, Lucas), shop equipment, and PPE shipped straight to your bay doors.</p>
        <p>Call <strong>(727) 609-8404</strong> or email <strong>sales@hathoro.com</strong> to set up recurring fleet replenishment.</p>
      `
    },
    'product-sourcing': {
      group: 'Business & B2B Services',
      title: 'Custom Product Sourcing Requests',
      subtitle: 'Need an industrial part or MRO supply not yet listed on our website? We can source it from our 1,000,000+ SKU distribution network.',
      html: `
        <h3>Access Over 1,000,000+ Industrial SKUs</h3>
        <p>Even if a specific manufacturer part number isn't displayed in our online catalog yet, our industrial distribution network likely has it in stock for immediate dispatch. Tell us the Brand and MFR Part # below and we will source it for you.</p>
        <form class="hathoro-info-form" onsubmit="event.preventDefault();this.innerHTML='<p style=\\'color:#15803d;font-weight:700;margin:0;\\'>✓ Sourcing Request Submitted! Our catalog team will check warehouse stock and email your quote within 1 business day.</p>';">
          <div>
            <label>Manufacturer Brand &amp; Part Number (MFR #)</label>
            <input type="text" required placeholder="e.g. 3M 8210 or Klein D213-9NE...">
          </div>
          <div>
            <label>Quantity Needed &amp; Destination ZIP Code</label>
            <input type="text" required placeholder="e.g. 24 units to ZIP 33701">
          </div>
          <div>
            <label>Your Email Address</label>
            <input type="email" required placeholder="you@company.com">
          </div>
          <button type="submit">Submit Sourcing Request →</button>
        </form>
      `
    },
    'privacy-policy': {
      group: 'Legal & Policies',
      title: 'Privacy Policy',
      subtitle: 'How Hathoro protects your personal and commercial account information.',
      html: `
        <h3>Information We Collect &amp; Protect</h3>
        <p>Hathoro collects only the information necessary to process, fulfill, and support your orders—including your name, company name, billing and shipping addresses, email address, and phone number. Payment transactions are encrypted and processed securely through Shopify PCI-DSS compliant payment gateways.</p>
        <h3>Zero Data Selling Guarantee</h3>
        <p>We never sell, rent, or trade your personal or business contact information to third-party marketers. Your data is used strictly for order fulfillment, carrier tracking notifications, and customer support.</p>
      `
    },
    'terms-of-service': {
      group: 'Legal & Policies',
      title: 'Terms of Service',
      subtitle: 'Commercial terms and conditions governing purchases on Hathoro.',
      html: `
        <h3>1. Order Acceptance &amp; Pricing</h3>
        <p>All orders placed on Hathoro are subject to product availability and verification. While we strive for 100% catalog accuracy, Hathoro reserves the right to correct typographical or pricing errors prior to dispatch.</p>
        <h3>2. Title, Risk of Loss &amp; Returns</h3>
        <p>All shipments are covered against transit damage under our 30-Day Hathoro Satisfaction Guarantee. Returns are accepted within 30 days of delivery in accordance with our Returns &amp; Exchanges Policy.</p>
      `
    }
  };

  function renderInfoHub(slug) {
    const page = HATHORO_INFO_PAGES[slug] || HATHORO_INFO_PAGES['about-hathoro'];
    const activeSlug = HATHORO_INFO_PAGES[slug] ? slug : 'about-hathoro';
    const groups = ['Company Information', 'Customer Support', 'Business & B2B Services', 'Legal & Policies'];

    const sidebarHtml = groups
      .map((grp) => {
        const items = Object.entries(HATHORO_INFO_PAGES)
          .filter(([, p]) => p.group === grp)
          .map(([k, p]) => {
            const activeCls = k === activeSlug ? ' hathoro-info-sidebar__link--active' : '';
            return `<li><a href="?hathoro_page=${k}" data-hathoro-page="${k}" class="hathoro-info-sidebar__link${activeCls}">${p.title.replace('Hathoro Industrial & Commercial Supply', 'About Hathoro')}</a></li>`;
          })
          .join('');
        return `
          <div class="hathoro-info-sidebar__group">
            <h4 class="hathoro-info-sidebar__heading">${grp}</h4>
            <ul class="hathoro-info-sidebar__list">${items}</ul>
          </div>
        `;
      })
      .join('');

    return `
      <div class="hathoro-breadcrumb">
        <div class="page-width">
          <ol class="hathoro-breadcrumb__list">
            <li><a href="/">Home</a></li>
            <li aria-hidden="true">/</li>
            <li><span>${page.group}</span></li>
            <li aria-hidden="true">/</li>
            <li aria-current="page">${page.title}</li>
          </ol>
        </div>
      </div>
      <section class="hathoro-info-hub">
        <div class="page-width hathoro-info-hub__grid">
          <aside class="hathoro-info-sidebar" aria-label="Information Directory">
            ${sidebarHtml}
          </aside>
          <article class="hathoro-info-article">
            <div class="hathoro-info-article__banner">
              <span class="hathoro-info-article__eyebrow">${page.group}</span>
              <h1 class="hathoro-info-article__title">${page.title}</h1>
              <p class="hathoro-info-article__subtitle">${page.subtitle}</p>
            </div>
            <div class="hathoro-info-article__body">
              ${page.html}
            </div>
          </article>
        </div>
      </section>
    `;
  }

  function initHathoroRouter() {
    const params = new URLSearchParams(window.location.search);
    const requestedPage = params.get('hathoro_page');
    const mainEl = document.getElementById('MainContent');
    if (requestedPage && mainEl && HATHORO_INFO_PAGES[requestedPage]) {
      mainEl.innerHTML = renderInfoHub(requestedPage);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    document.addEventListener('click', (e) => {
      const link = e.target.closest('a[href*="hathoro_page="], a[data-hathoro-page]');
      if (!link) return;
      const href = link.getAttribute('href') || '';
      let slug = link.getAttribute('data-hathoro-page');
      if (!slug && href.includes('hathoro_page=')) {
        try {
          const u = new URL(href, window.location.origin);
          slug = u.searchParams.get('hathoro_page');
        } catch (_) {}
      }
      if (slug && HATHORO_INFO_PAGES[slug] && mainEl) {
        e.preventDefault();
        const url = new URL(window.location.href);
        url.searchParams.set('hathoro_page', slug);
        window.history.pushState({ hathoro_page: slug }, '', url.toString());
        mainEl.innerHTML = renderInfoHub(slug);
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
    });

    window.addEventListener('popstate', () => {
      const p = new URLSearchParams(window.location.search).get('hathoro_page');
      if (p && HATHORO_INFO_PAGES[p] && mainEl) {
        mainEl.innerHTML = renderInfoHub(p);
      } else if (!p) {
        window.location.reload();
      }
    });
  }

  function initHathoroPDP() {
    initHathoroRouter();

    const descContainer = document.getElementById('HathoroFormattedDescription');
    const specsBody = document.getElementById('HathoroSpecsTableBody');
    const highlightsBox = document.getElementById('HathoroBuyboxHighlights');
    const highlightsList = document.getElementById('HathoroBuyboxHighlightsList');

    if (descContainer) {
      const styledNodes = descContainer.querySelectorAll('[style]');
      styledNodes.forEach((el) => {
        el.style.fontFamily = '';
        el.style.fontSize = '';
        el.style.lineHeight = '';
        el.style.color = '';
        el.style.backgroundColor = '';
        if (!el.getAttribute('style') || el.getAttribute('style').trim() === '') {
          el.removeAttribute('style');
        }
      });

      const existingKeys = new Set();
      if (specsBody) {
        specsBody.querySelectorAll('th').forEach((th) => {
          existingKeys.add(th.textContent.trim().toLowerCase().replace(/:$/, ''));
        });
      }

      const extractedSpecs = [];
      const highlightBullets = [];

      const embeddedTables = descContainer.querySelectorAll('table');
      embeddedTables.forEach((table) => {
        table.querySelectorAll('tr').forEach((tr) => {
          const cells = tr.querySelectorAll('th, td');
          if (cells.length === 2) {
            const key = cells[0].textContent.trim().replace(/:$/, '');
            const val = cells[1].textContent.trim();
            if (key && val && key.length <= 45 && !existingKeys.has(key.toLowerCase())) {
              existingKeys.add(key.toLowerCase());
              extractedSpecs.push({ key, val });
            }
          }
        });
        table.classList.add('hathoro-specs-table');
      });

      const listItems = descContainer.querySelectorAll('li');
      listItems.forEach((li) => {
        const rawText = li.textContent.trim();
        if (!rawText) {
          li.remove();
          return;
        }

        const colonIdx = rawText.indexOf(':');
        if (colonIdx > 1 && colonIdx < 38) {
          const keyPart = rawText.slice(0, colonIdx).trim();
          const valPart = rawText.slice(colonIdx + 1).trim();

          if (!li.querySelector('strong, b') && valPart.length > 0) {
            li.innerHTML = '<strong>' + escapeHtml(keyPart) + ':</strong> ' + escapeHtml(valPart);
          }

          if (valPart.length > 0 && valPart.length <= 95 && !existingKeys.has(keyPart.toLowerCase())) {
            existingKeys.add(keyPart.toLowerCase());
            extractedSpecs.push({ key: keyPart, val: valPart });
          }
        }

        if (highlightBullets.length < 4 && rawText.length > 12 && rawText.length < 180) {
          highlightBullets.push(li.innerHTML);
        }
      });

      if (highlightBullets.length === 0) {
        const paragraphs = Array.from(descContainer.querySelectorAll('p'))
          .map((p) => p.textContent.trim())
          .filter((t) => t.length > 20);
        if (paragraphs.length > 0) {
          const sentences = paragraphs
            .join(' ')
            .split(/(?<=[.!?])\s+/)
            .map((s) => s.trim())
            .filter((s) => s.length > 20 && s.length < 160);
          sentences.slice(0, 3).forEach((s) => highlightBullets.push(escapeHtml(s)));
        }
      }

      if (specsBody && extractedSpecs.length > 0) {
        extractedSpecs.slice(0, 14).forEach((item) => {
          const tr = document.createElement('tr');
          const th = document.createElement('th');
          th.textContent = item.key;
          const td = document.createElement('td');
          td.textContent = item.value || item.val;
          tr.appendChild(th);
          tr.appendChild(td);
          specsBody.appendChild(tr);
        });
      }

      if (highlightsBox && highlightsList && highlightBullets.length > 0) {
        highlightsList.innerHTML = highlightBullets.map((b) => '<li>' + b + '</li>').join('');
        highlightsBox.removeAttribute('hidden');
      }
    }

    document.querySelectorAll('[data-hathoro-scroll]').forEach((link) => {
      link.addEventListener('click', (e) => {
        const targetSelector = link.getAttribute('href');
        const targetEl = targetSelector ? document.querySelector(targetSelector) : null;
        if (targetEl) {
          e.preventDefault();
          targetEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      });
    });

    const stickyBar = document.getElementById('HathoroStickyATC');
    const mainSubmitBtn = document.querySelector('.product-form__submit');
    const stickyTriggerBtn = document.getElementById('HathoroStickyATCButton');

    if (stickyBar && mainSubmitBtn) {
      const toggleStickyBar = () => {
        const rect = mainSubmitBtn.getBoundingClientRect();
        const scrolledPast = rect.bottom < 0;
        stickyBar.classList.toggle('is-visible', scrolledPast);
      };
      window.addEventListener('scroll', toggleStickyBar, { passive: true });
      toggleStickyBar();

      if (stickyTriggerBtn) {
        stickyTriggerBtn.addEventListener('click', () => {
          if (!mainSubmitBtn.disabled) {
            mainSubmitBtn.click();
          } else {
            mainSubmitBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
        });
      }
    }
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initHathoroPDP);
  } else {
    initHathoroPDP();
  }
})();
