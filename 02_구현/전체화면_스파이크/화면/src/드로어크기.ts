import {useLayoutEffect,useState} from 'react';
import type {RefObject} from 'react';

export interface DrawerSpace {viewportHeight:number;viewportWidth:number;composerHeight:number;composerGap:number;popupTop:number|null;topClearance?:number}
export function drawerBounds(space:DrawerSpace){
 const low=space.viewportHeight<=560;
 // Leave the real composer, its toggle gap, and a readable popup above the drawer.
 const reserve=Math.max(space.topClearance??16,space.popupTop===null?16:space.popupTop+12+(low?92:140));
 const max=Math.max(0,Math.floor(space.viewportHeight-space.composerHeight-space.composerGap-reserve));
 return {min:Math.min(low?184:252,max),max};
}
export function effectiveDrawerHeight(preferred:number,bounds:{min:number;max:number}){
 return Math.max(bounds.min,Math.min(bounds.max,preferred));
}
// UI-only preference: closing, replay ticks and temporary space limits never reset it.
export function useDrawerHeight(surfaceRef:RefObject<HTMLElement|null>,composerRef:RefObject<HTMLDivElement|null>,popupOpen:boolean){
 const [preferred,setPreferred]=useState(()=>typeof window==='undefined'?360:Math.min(360,window.innerHeight*.4));
 const [space,setSpace]=useState<DrawerSpace>(()=>({viewportHeight:typeof window==='undefined'?900:window.innerHeight,viewportWidth:typeof window==='undefined'?1440:window.innerWidth,composerHeight:110,composerGap:36,popupTop:null}));
 useLayoutEffect(()=>{
  const surface=surfaceRef.current,composer=composerRef.current;if(!surface||!composer)return;
  const measure=()=>{
   const popup=surface.querySelector<HTMLElement>('.popup');
   const inputRect=composer.getBoundingClientRect();
   const menuRect=surface.querySelector<HTMLElement>('.menu-toggle')?.getBoundingClientRect();
   // A full-width input must not cover the menu when the drawer reaches its maximum.
   const topClearance=menuRect&&inputRect.left<menuRect.right&&inputRect.right>menuRect.left?menuRect.bottom+8:16;
   const next:DrawerSpace={viewportHeight:window.innerHeight,viewportWidth:window.innerWidth,composerHeight:inputRect.height,composerGap:parseFloat(getComputedStyle(surface).getPropertyValue('--composer-lift-gap'))||36,popupTop:popup?.getBoundingClientRect().top??null,topClearance};
   setSpace(old=>Object.keys(next).every(key=>old[key as keyof DrawerSpace]===next[key as keyof DrawerSpace])?old:next);
  };
  measure();const observer=new ResizeObserver(measure);observer.observe(composer);
  window.addEventListener('resize',measure);
  return()=>{observer.disconnect();window.removeEventListener('resize',measure);};
 },[popupOpen]);
 const bounds=drawerBounds(space);
 return {height:effectiveDrawerHeight(preferred,bounds),...bounds,setPreferred};
}

export function draggedHeight(startHeight:number,startY:number,currentY:number){return startHeight+startY-currentY;}
export function keyboardHeight(key:string,height:number,bounds:{min:number;max:number},shift=false):number|null{
 const step=shift?50:20;
 const next=key==='Home'?bounds.min:key==='End'?bounds.max:key==='ArrowUp'?height+step:key==='ArrowDown'?height-step:null;
 return next===null?null:effectiveDrawerHeight(next,bounds);
}
