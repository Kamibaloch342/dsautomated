/**
 * Looping product row (sections/hathoro-product-marquee.liquid), 14 Nov 2026.
 * The row glides left; when the first card has fully left the screen it is moved to the end
 * of the row, so the loop never ends and no card is duplicated (each Add to cart works).
 * Pauses on hover, on keyboard focus, on touch (resumes 3 s later), while off screen and in a
 * hidden tab. Visitors who ask for reduced motion get a row they can scroll by hand.
 */
(function () {
  if (customElements.get('hathoro-marquee')) return;

  class HathoroMarquee extends HTMLElement {
    connectedCallback() {
      this.track = this.querySelector('.hathoro-marquee__track');
      if (!this.track) return;
      this.speed = parseFloat(this.dataset.speed || '40');
      this.offset = 0;
      this.last = null;
      this.hovered = false;
      this.focused = false;
      this.touchHold = false;
      this.visible = true;

      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        this.classList.add('is-manual');
        return;
      }

      // Real mouse only: a tap on a phone also fires mouseenter, with no mouseleave until the
      // next tap elsewhere, which would leave the row paused.
      this.addEventListener('pointerenter', (e) => {
        if (e.pointerType === 'mouse') this.hovered = true;
      });
      this.addEventListener('pointerleave', (e) => {
        if (e.pointerType === 'mouse') this.hovered = false;
      });
      this.addEventListener('focusin', () => (this.focused = true));
      this.addEventListener('focusout', () => (this.focused = false));
      this.addEventListener(
        'touchstart',
        () => {
          this.touchHold = true;
          clearTimeout(this.touchTimer);
        },
        { passive: true }
      );
      this.addEventListener(
        'touchend',
        () => {
          clearTimeout(this.touchTimer);
          this.touchTimer = setTimeout(() => (this.touchHold = false), 3000);
        },
        { passive: true }
      );

      if ('IntersectionObserver' in window) {
        this.observer = new IntersectionObserver((entries) => {
          this.visible = entries[0].isIntersecting;
        });
        this.observer.observe(this);
      }

      this.step = this.step.bind(this);
      this.frame = requestAnimationFrame(this.step);
    }

    disconnectedCallback() {
      cancelAnimationFrame(this.frame);
      if (this.observer) this.observer.disconnect();
    }

    gap() {
      const g = parseFloat(getComputedStyle(this.track).columnGap);
      return isNaN(g) ? 0 : g;
    }

    step(time) {
      if (this.last === null) this.last = time;
      // Cap the step so a tab coming back from the background does not jump.
      const dt = Math.min((time - this.last) / 1000, 0.1);
      this.last = time;

      const overflowing = this.track.scrollWidth > this.clientWidth + 8;
      this.classList.toggle('is-static', !overflowing);

      const paused = this.hovered || this.focused || this.touchHold || !this.visible || document.hidden;
      if (overflowing && !paused) {
        this.offset += this.speed * dt;
        const first = this.track.firstElementChild;
        if (first) {
          const width = first.getBoundingClientRect().width + this.gap();
          if (width > 0 && this.offset >= width) {
            this.track.appendChild(first);
            this.offset -= width;
          }
        }
        this.track.style.transform = 'translate3d(' + -this.offset + 'px, 0, 0)';
      }
      this.frame = requestAnimationFrame(this.step);
    }
  }

  customElements.define('hathoro-marquee', HathoroMarquee);
})();
