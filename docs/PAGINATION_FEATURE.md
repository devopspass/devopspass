# Docs Pagination Feature

## Overview
Added pagination support for the Docs section in the UI. When search results exceed 25 documents, only the first batch is displayed initially, with subsequent batches loaded on demand.

## Features

### 1. **Batch Loading (25 docs per page)**
   - Initial search displays first 25 docs
   - Remaining docs load in batches of 25 as needed
   - Both infinite scroll and "Load More" button modes supported

### 2. **Dual Pagination Modes**
   - **Infinite Scroll (default)**
     - Automatically loads next batch when user scrolls within 200px of bottom
     - Smooth, seamless experience
     - Loading indicator shows at bottom while fetching
   
   - **Load More Button**
     - Manual loading with explicit button click
     - User can control pagination pace
     - Better for slower connections or large result sets

### 3. **User Preference Storage**
   - User's pagination mode preference saved to browser localStorage
   - Toggle button to switch between modes
   - Preference persists across sessions

### 4. **Visual Feedback**
   - Status bar shows: "Showing X of Y docs"
   - Loading indicator with spinner when fetching more docs
   - Toggle button displays current mode
   - "Load More" button disabled while loading

## Implementation Details

### TypeScript Changes (`ui/src/app/app.component.ts`)

**New Properties:**
```typescript
displayedDocs: StoredDoc[] = [];         // Currently visible docs
docsCurrentPage = 0;                      // Current page index
docsPerPage = 25;                         // Batch size
docsLoadingMore = false;                  // Loading state
docsUseInfiniteScroll = true;            // User preference
@ViewChild('docsGridContainer') docsGridContainer?: ElementRef<HTMLElement>;
private docsScrollListener: (() => void) | null = null;
```

**New Methods:**
- `loadMoreDocs()` - Loads next batch and appends to displayed docs
- `setupDocsScrollListener()` - Attaches scroll event listener to docs container
- `removeDocsScrollListener()` - Cleans up scroll listener
- `loadMoreDocsWithScroll()` - Triggered on scroll threshold
- `loadDocsPreferences()` - Loads user preference from localStorage
- `toggleDocsScrollMode()` - Switches between infinite scroll and load-more button
- `loadMoreDocsManual()` - Manual "Load More" button handler

**Modified Methods:**
- `searchDocs()` - Now resets pagination, loads first batch, and sets up scroll listener
- `ngOnInit()` - Now calls `loadDocsPreferences()`
- `ngOnDestroy()` - Now removes scroll listener

### HTML Changes (`ui/src/app/app.component.html`)

**Template Updates:**
```html
<!-- Docs grid with pagination -->
<div class="docs-grid" #docsGridContainer>
  <div *ngFor="let doc of displayedDocs">
    <!-- Doc card content -->
  </div>
</div>

<!-- Pagination controls footer -->
<div class="docs-pagination-footer" *ngIf="docs.length > docsPerPage">
  <div class="pagination-status">
    Showing {{ displayedDocs.length }} of {{ docs.length }} docs
  </div>
  <div class="pagination-controls">
    <!-- Mode toggle button -->
    <button (click)="toggleDocsScrollMode()">
      {{ docsUseInfiniteScroll ? 'Infinite Scroll' : 'Load More Button' }}
    </button>
    <!-- Load More button (only visible in Load More mode) -->
    <button *ngIf="!docsUseInfiniteScroll" (click)="loadMoreDocsManual()">
      Load More
    </button>
  </div>
</div>

<!-- Loading indicator for infinite scroll -->
<div class="docs-loading-indicator" *ngIf="docsLoadingMore && docsUseInfiniteScroll">
  <span class="spinner"></span> Loading more docs...
</div>
```

### CSS Changes (`ui/src/app/app.component.css`)

**Docs Grid:**
- Added `overflow-y: auto` for vertical scrolling
- Added `max-height: calc(100vh - 300px)` to constrain height
- Added `padding-right: 8px` for scrollbar spacing

**New Styles:**
- `.docs-pagination-footer` - Control bar styling
- `.pagination-status` - Status text
- `.pagination-controls` - Button container
- `.pagination-toggle` - Mode toggle button
- `.pagination-load-more` - Load More button
- `.docs-loading-indicator` - Loading state indicator
- `.spinner-container` & `.loading-text` - Loading visual feedback

## User Experience

### With Infinite Scroll Enabled:
1. User searches for docs (e.g., 150 results)
2. First 25 docs display immediately
3. As user scrolls down, more docs auto-load in batches
4. Spinner appears briefly during loading
5. Seamless experience with no manual intervention

### With Load More Button Enabled:
1. User searches for docs
2. First 25 docs display with pagination footer
3. Footer shows "Showing 25 of 150 docs"
4. User clicks "Load More" to fetch next batch
5. Button disabled during loading
6. Next 25 docs append to list

## Browser Compatibility
- Uses standard DOM scroll events
- localStorage API for preference persistence
- Angular 17 features (standalone components, ViewChild)
- Works in all modern browsers (Chrome, Firefox, Safari, Edge)

## Performance Considerations
- Frontend pagination (not API-based) - all results fetched once
- Only visible docs rendered in initial view
- Scroll listener attached only when needed
- Event listener cleaned up on component destroy
- ngZone.runOutsideAngular() used for scroll events to minimize change detection

## Future Enhancements
- API-side pagination (pagination parameters in backend)
- Configurable batch size
- Search/filter within displayed results
- Virtual scrolling for very large result sets (1000+ docs)
- Remember last scroll position
