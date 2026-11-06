import {expect,it} from 'vitest';
import {drawerBounds,effectiveDrawerHeight,draggedHeight,keyboardHeight} from './드로어크기';
it('좁은 창에서 입력이 메뉴와 가로로 겹치면 메뉴 아래까지 공간을 남긴다',()=>{
 const space={viewportHeight:844,viewportWidth:390,composerHeight:110,composerGap:36,popupTop:null,topClearance:68};
 const bounds=drawerBounds(space);
 expect(space.viewportHeight-bounds.max-space.composerHeight-space.composerGap).toBeGreaterThanOrEqual(68);
});
it('낮은 창의 팝업에도 헤더뿐 아니라 읽을 본문 공간 92픽셀을 남긴다',()=>{
 const bounds=drawerBounds({viewportHeight:390,viewportWidth:844,composerHeight:80,composerGap:31,popupTop:12});
 expect(bounds).toEqual({min:163,max:163});
});
it('상단 경계는 위로 끌면 커지고 아래로 끌면 작아진다',()=>{
 expect(draggedHeight(360,540,410)).toBe(490);
 expect(draggedHeight(490,410,470)).toBe(430);
});
it('키보드는 위/아래·Home/End를 높이 범위 안에서 조정하고 다른 키는 무시한다',()=>{
 const bounds={min:252,max:738};
 expect(keyboardHeight('ArrowUp',360,bounds)).toBe(380);
 expect(keyboardHeight('ArrowDown',360,bounds)).toBe(340);
 expect(keyboardHeight('ArrowUp',360,bounds,true)).toBe(410);
 expect(keyboardHeight('Home',650,bounds)).toBe(252);
 expect(keyboardHeight('End',360,bounds)).toBe(738);
 expect(keyboardHeight('ArrowDown',252,bounds)).toBe(252);
 expect(keyboardHeight('ArrowUp',738,bounds)).toBe(738);
 expect(keyboardHeight('Enter',360,bounds)).toBeNull();
});
it('입력과 팝업 공간은 유효 높이만 제한하고 선호 높이를 덮지 않는다',()=>{
 const space={viewportHeight:900,viewportWidth:1440,composerHeight:110,composerGap:36,popupTop:null};
 expect(drawerBounds(space)).toEqual({min:252,max:738});
 const popup=drawerBounds({...space,popupTop:72});
 expect(popup).toEqual({min:252,max:530});
 const preferred=650;
 expect(effectiveDrawerHeight(preferred,popup)).toBe(530);
 expect(effectiveDrawerHeight(preferred,drawerBounds(space))).toBe(650);
 expect(effectiveDrawerHeight(40,popup)).toBe(252);
 const low=drawerBounds({viewportHeight:390,viewportWidth:844,composerHeight:80,composerGap:31,popupTop:null});
 expect(low).toEqual({min:184,max:263});
});
