var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Directive, Input, HostListener, ElementRef, Renderer2, inject } from '@angular/core';
let TooltipDirective = class TooltipDirective {
    constructor() {
        this.el = inject(ElementRef);
        this.renderer = inject(Renderer2);
        this.tooltipText = '';
        this.tooltipPosition = 'top';
        this.tooltipElement = null;
    }
    showTooltip() {
        if (!this.tooltipText || this.tooltipElement)
            return;
        this.tooltipElement = this.renderer.createElement('div');
        this.tooltipElement.className = `tooltip tooltip-${this.tooltipPosition}`;
        this.tooltipElement.textContent = this.tooltipText;
        this.tooltipElement.setAttribute('role', 'tooltip');
        this.renderer.appendChild(document.body, this.tooltipElement);
        const hostRect = this.el.nativeElement.getBoundingClientRect();
        const tooltipRect = this.tooltipElement.getBoundingClientRect();
        let top;
        let left;
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
    hideTooltip() {
        if (this.tooltipElement) {
            this.renderer.removeChild(document.body, this.tooltipElement);
            this.tooltipElement = null;
        }
    }
    ngOnDestroy() {
        this.hideTooltip();
    }
};
__decorate([
    Input('appTooltip')
], TooltipDirective.prototype, "tooltipText", void 0);
__decorate([
    Input()
], TooltipDirective.prototype, "tooltipPosition", void 0);
__decorate([
    HostListener('mouseenter'),
    HostListener('focus')
], TooltipDirective.prototype, "showTooltip", null);
__decorate([
    HostListener('mouseleave'),
    HostListener('blur')
], TooltipDirective.prototype, "hideTooltip", null);
TooltipDirective = __decorate([
    Directive({
        selector: '[appTooltip]',
        standalone: true
    })
], TooltipDirective);
export { TooltipDirective };
//# sourceMappingURL=tooltip.directive.js.map