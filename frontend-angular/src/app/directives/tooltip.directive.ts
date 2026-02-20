import { Directive, Input, HostBinding, HostListener, ElementRef, Renderer2, inject } from '@angular/core';

@Directive({
  selector: '[appTooltip]',
  standalone: true
})
export class TooltipDirective {
  private el = inject(ElementRef);
  private renderer = inject(Renderer2);

  @Input('appTooltip') tooltipText = '';
  @Input() tooltipPosition: 'top' | 'bottom' | 'left' | 'right' = 'top';

  private tooltipElement: HTMLElement | null = null;

  @HostListener('mouseenter')
  @HostListener('focus')
  showTooltip() {
    if (!this.tooltipText || this.tooltipElement) return;
    
    this.tooltipElement = this.renderer.createElement('div');
    this.tooltipElement.className = `tooltip tooltip-${this.tooltipPosition}`;
    this.tooltipElement.textContent = this.tooltipText;
    this.tooltipElement.setAttribute('role', 'tooltip');
    
    this.renderer.appendChild(document.body, this.tooltipElement);
    
    const hostRect = this.el.nativeElement.getBoundingClientRect();
    const tooltipRect = this.tooltipElement.getBoundingClientRect();
    
    let top: number;
    let left: number;
    
    switch (this.tooltipPosition) {
      case 'bottom':
        top = hostRect.bottom + 8;
        left = hostRect.left + (hostRect.width - tooltipRect.width) / 2;
        break;
      case 'left':
        top = hostRect.top + (hostRect.height - tooltipRect.height) / 2;
        left = hostRect.left - tooltipRect.width - 8;
        break;
      case 'right':
        top = hostRect.top + (hostRect.height - tooltipRect.height) / 2;
        left = hostRect.right + 8;
        break;
      default:
        top = hostRect.top - tooltipRect.height - 8;
        left = hostRect.left + (hostRect.width - tooltipRect.width) / 2;
    }
    
    this.renderer.setStyle(this.tooltipElement, 'top', `${top}px`);
    this.renderer.setStyle(this.tooltipElement, 'left', `${left}px`);
  }

  @HostListener('mouseleave')
  @HostListener('blur')
  hideTooltip() {
    if (this.tooltipElement) {
      this.renderer.removeChild(document.body, this.tooltipElement);
      this.tooltipElement = null;
    }
  }

  ngOnDestroy() {
    this.hideTooltip();
  }
}
